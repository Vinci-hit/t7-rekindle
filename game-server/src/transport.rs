mod rooms;

use std::{collections::HashMap, collections::VecDeque, io, sync::Arc, time::Duration};

use axum::{
    Json, Router,
    extract::{State, WebSocketUpgrade, ws::Message, ws::WebSocket},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use futures_util::{SinkExt, StreamExt, stream::SplitSink};
use serde_json::json;
use tokio::{
    sync::{Notify, Semaphore, mpsc, oneshot, watch},
    task::JoinHandle,
    time::{Instant, MissedTickBehavior},
};

use crate::{
    lobby::Lobby,
    maps::MapCatalog,
    protocol::{
        ClientCommand, ClientRequest, ErrorCode, MAX_REQUEST_BYTES, ServerEvent, ServerMessage,
        VERSION,
    },
    room::RoomSnapshot,
};

use crate::auth::{Admission, GuestIdentity, GuestRequest};
use crate::limits::{COMMAND_QUEUE_SIZE, MAX_CONNECTIONS, OUTPUT_QUEUE_SIZE};
const CACHE_SIZE: usize = 32;
const REQUESTS_PER_SECOND: usize = 64;

#[derive(Clone)]
struct AppState {
    commands: mpsc::Sender<Command>,
    shutdown: watch::Receiver<bool>,
    dev_lobby: bool,
    admission: Option<Arc<Admission>>,
    slots: Arc<Semaphore>,
}

pub struct GameServer {
    router: Router,
    shutdown: watch::Sender<bool>,
    task: Option<JoinHandle<()>>,
}

impl GameServer {
    /// Development identity is available only when explicitly enabled by the caller.
    /// The executable additionally requires a loopback listener.
    pub fn new(dev_lobby: bool) -> Self {
        Self::with_maps(dev_lobby, MapCatalog::default())
    }

    pub fn with_maps(dev_lobby: bool, maps: MapCatalog) -> Self {
        Self::with_admission(dev_lobby, maps, None)
    }

    pub fn with_admission(
        dev_lobby: bool,
        maps: MapCatalog,
        admission: Option<Arc<Admission>>,
    ) -> Self {
        Self::with_configuration(
            dev_lobby,
            maps,
            admission,
            crate::limits::RoomRules::default(),
        )
    }

    pub fn with_configuration(
        dev_lobby: bool,
        maps: MapCatalog,
        admission: Option<Arc<Admission>>,
        rules: crate::limits::RoomRules,
    ) -> Self {
        Self::with_pve_configuration(
            dev_lobby,
            maps,
            admission,
            rules,
            crate::pve::Catalog::default(),
        )
    }

    pub fn with_pve_configuration(
        dev_lobby: bool,
        maps: MapCatalog,
        admission: Option<Arc<Admission>>,
        rules: crate::limits::RoomRules,
        pve: crate::pve::Catalog,
    ) -> Self {
        let (commands, receiver) = mpsc::channel(COMMAND_QUEUE_SIZE);
        let (shutdown, shutdown_rx) = watch::channel(false);
        let state = AppState {
            commands,
            shutdown: shutdown_rx.clone(),
            dev_lobby,
            admission,
            slots: Arc::new(Semaphore::new(MAX_CONNECTIONS)),
        };
        let router = Router::new()
            .route("/health", get(health))
            .route("/ready", get(health))
            .route("/ws", get(upgrade))
            .route("/ws/motion", get(upgrade_motion))
            .route("/ws/resume", get(upgrade_resume))
            .route(
                "/auth/guest",
                post(guest).layer(axum::extract::DefaultBodyLimit::max(4096)),
            )
            .route("/dev", get(dev_page))
            .route("/dev/lobby.js", get(dev_script))
            .route("/dev/lobby.css", get(dev_style))
            .with_state(state);
        let coordinator = Coordinator {
            lobby: Lobby::with_rules(maps, rules).with_pve_catalog(pve),
            ..Coordinator::default()
        };
        let task = tokio::spawn(coordinator.run(receiver, shutdown_rx));
        Self {
            router,
            shutdown,
            task: Some(task),
        }
    }

    pub fn router(&self) -> Router {
        self.router.clone()
    }

    pub fn shutdown_receiver(&self) -> watch::Receiver<bool> {
        self.shutdown.subscribe()
    }

    pub fn shutdown_handle(&self) -> watch::Sender<bool> {
        self.shutdown.clone()
    }

    pub fn shutdown(&self) {
        self.shutdown.send_replace(true);
    }

    pub async fn stopped(mut self) -> io::Result<()> {
        self.shutdown();
        if let Some(task) = self.task.take() {
            task.await.map_err(io::Error::other)?;
        }
        Ok(())
    }
}

impl Drop for GameServer {
    fn drop(&mut self) {
        self.shutdown();
        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}

pub async fn wait_for_shutdown(mut shutdown: watch::Receiver<bool>) {
    while !*shutdown.borrow_and_update() {
        if shutdown.changed().await.is_err() {
            break;
        }
    }
}

async fn health(State(state): State<AppState>) -> impl IntoResponse {
    let ready = !*state.shutdown.borrow() && !state.commands.is_closed();
    (
        if ready {
            StatusCode::OK
        } else {
            StatusCode::SERVICE_UNAVAILABLE
        },
        Json(json!({
            "ready": ready,
            "protocolVersion": VERSION,
            "mode": if state.admission.is_some() { "guestLobby" } else if state.dev_lobby { "developmentLobby" } else { "healthOnly" },
            "productionAdmission": false,
            "guestAdmission": state.admission.is_some(),
        })),
    )
}

async fn dev_asset(state: AppState, content_type: &'static str, body: &'static str) -> Response {
    if !state.dev_lobby || *state.shutdown.borrow() || state.commands.is_closed() {
        return StatusCode::NOT_FOUND.into_response();
    }
    (
        [
            (header::CONTENT_TYPE, content_type),
            (header::CACHE_CONTROL, "no-store"),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
            (
                header::CONTENT_SECURITY_POLICY,
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            ),
        ],
        body,
    ).into_response()
}

async fn dev_page(State(state): State<AppState>) -> Response {
    dev_asset(
        state,
        "text/html; charset=utf-8",
        include_str!("../dev/index.html"),
    )
    .await
}

async fn dev_script(State(state): State<AppState>) -> Response {
    dev_asset(
        state,
        "text/javascript; charset=utf-8",
        include_str!("../dev/lobby.js"),
    )
    .await
}

async fn dev_style(State(state): State<AppState>) -> Response {
    dev_asset(
        state,
        "text/css; charset=utf-8",
        include_str!("../dev/lobby.css"),
    )
    .await
}

async fn guest(
    State(state): State<AppState>,
    axum::extract::ConnectInfo(peer): axum::extract::ConnectInfo<std::net::SocketAddr>,
    Json(request): Json<GuestRequest>,
) -> Response {
    let Some(admission) = state.admission.clone() else {
        return StatusCode::NOT_FOUND.into_response();
    };
    if !admission.allow_attempt(&peer.ip().to_string()) {
        return StatusCode::TOO_MANY_REQUESTS.into_response();
    }
    match admission.issue(request) {
        Some(reply) => ([(header::CACHE_CONTROL, "no-store")], Json(reply)).into_response(),
        None => StatusCode::BAD_REQUEST.into_response(),
    }
}

async fn upgrade(
    State(state): State<AppState>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    if (!state.dev_lobby && state.admission.is_none())
        || *state.shutdown.borrow()
        || state.commands.is_closed()
    {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    // Browser clients must originate from this service. Native gateways do not
    // send Origin; this check does not replace their future ticket admission.
    if let Some(origin) = headers.get(header::ORIGIN) {
        let host = headers
            .get(header::HOST)
            .and_then(|value| value.to_str().ok());
        if !host.is_some_and(|host| {
            origin.to_str().ok().is_some_and(|origin| {
                origin == format!("http://{host}") || origin == format!("https://{host}")
            })
        }) {
            return StatusCode::FORBIDDEN.into_response();
        }
    }
    let motion_key = headers
        .get("x-t7-motion-key")
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned);
    if motion_key
        .as_ref()
        .is_some_and(|key| !valid_motion_key(key))
    {
        return StatusCode::BAD_REQUEST.into_response();
    }
    let authorized = if let Some(admission) = &state.admission {
        let token = headers
            .get(header::AUTHORIZATION)
            .and_then(|h| h.to_str().ok())
            .and_then(|h| h.strip_prefix("Bearer "));
        let Some(account) = token.and_then(|token| admission.redeem(token)) else {
            return StatusCode::UNAUTHORIZED.into_response();
        };
        Some(account)
    } else {
        None
    };
    let Ok(permit) = state.slots.clone().try_acquire_owned() else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    ws.max_message_size(MAX_REQUEST_BYTES)
        .max_frame_size(MAX_REQUEST_BYTES)
        .on_upgrade(move |socket| async move {
            let _permit = permit;
            connection(socket, state, authorized, motion_key, None, None).await;
        })
}

// A retained channel capability resumes the same identity; login tickets stay one-use.
async fn upgrade_resume(
    State(state): State<AppState>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    if *state.shutdown.borrow() || headers.contains_key(header::ORIGIN) {
        return StatusCode::FORBIDDEN.into_response();
    }
    let Some(key) = headers
        .get("x-t7-motion-key")
        .and_then(|h| h.to_str().ok())
        .filter(|key| valid_motion_key(key))
    else {
        return StatusCode::UNAUTHORIZED.into_response();
    };
    let Ok(permit) = state.slots.clone().try_acquire_owned() else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let generation = uuid::Uuid::new_v4().to_string();
    let (outgoing, output) = mpsc::channel(OUTPUT_QUEUE_SIZE);
    let (closing, closing_rx) = watch::channel(false);
    let (accepted, acceptance) = oneshot::channel();
    if state
        .commands
        .try_send(Command::Resume {
            key: key.to_owned(),
            generation: generation.clone(),
            outgoing: outgoing.clone(),
            closing,
            accepted,
        })
        .is_err()
    {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    let Ok(Ok(Some(id))) = tokio::time::timeout(Duration::from_secs(2), acceptance).await else {
        return StatusCode::UNAUTHORIZED.into_response();
    };
    let cleanup = state.commands.clone();
    let failed_id = id.clone();
    let failed_generation = generation.clone();
    ws.max_message_size(MAX_REQUEST_BYTES)
        .max_frame_size(MAX_REQUEST_BYTES)
        .on_failed_upgrade(move |_| {
            tokio::spawn(async move {
                let _ = cleanup
                    .send(Command::Detached {
                        id: failed_id,
                        generation: failed_generation,
                        reason: "resume upgrade failed",
                    })
                    .await;
            });
        })
        .on_upgrade(move |socket| async move {
            let _permit = permit;
            connection(
                socket,
                state,
                None,
                None,
                None,
                Some(ResumeSocket {
                    id,
                    generation,
                    output,
                    outgoing,
                    closing_rx,
                }),
            )
            .await;
        })
}

struct ResumeSocket {
    id: String,
    generation: String,
    output: mpsc::Receiver<ServerEvent>,
    outgoing: mpsc::Sender<ServerEvent>,
    closing_rx: watch::Receiver<bool>,
}

fn valid_motion_key(key: &str) -> bool {
    key.len() == 64 && key.bytes().all(|b| b.is_ascii_hexdigit())
}

async fn upgrade_motion(
    State(state): State<AppState>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    if *state.shutdown.borrow() || state.commands.is_closed() {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    // This is a per-session capability, not a second redemption of the login ticket.
    // Native gateways use headers; browser cross-origin attachment is forbidden.
    if headers.contains_key(header::ORIGIN) {
        return StatusCode::FORBIDDEN.into_response();
    }
    let Some(key) = headers
        .get("x-t7-motion-key")
        .and_then(|v| v.to_str().ok())
        .filter(|v| valid_motion_key(v))
    else {
        return StatusCode::UNAUTHORIZED.into_response();
    };
    let Ok(permit) = state.slots.clone().try_acquire_owned() else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let key = key.to_owned();
    let (poses, latest) = watch::channel(None);
    let (wake, wake_rx) = watch::channel(Arc::new(Notify::new()));
    // Validate before upgrading; reject unknown or already attached sessions.
    let (accepted, acceptance) = oneshot::channel();
    let output = Arc::new(MotionOutput::default());
    let outgoing = output.clone();
    let (closing, closing_rx) = watch::channel(false);
    if state
        .commands
        .try_send(Command::AttachMotion {
            key,
            latest,
            wake,
            outgoing,
            closing,
            accepted,
        })
        .is_err()
    {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    let Ok(Ok(Some(id))) = tokio::time::timeout(Duration::from_secs(2), acceptance).await else {
        return StatusCode::UNAUTHORIZED.into_response();
    };
    let cleanup = state.commands.clone();
    let failed_id = id.clone();
    let failed_output = output.clone();
    ws.max_message_size(MAX_REQUEST_BYTES)
        .max_frame_size(MAX_REQUEST_BYTES)
        .on_failed_upgrade(move |_| {
            tokio::spawn(async move {
                let _ = cleanup
                    .send(Command::MotionClosed {
                        id: failed_id,
                        output: failed_output,
                    })
                    .await;
            });
        })
        .on_upgrade(move |socket| async move {
            let _permit = permit;
            connection(
                socket,
                state,
                None,
                None,
                Some((
                    id,
                    output,
                    closing_rx,
                    MotionSender {
                        poses,
                        wake: wake_rx,
                    },
                )),
                None,
            )
            .await;
        })
}

type MotionSocket = (
    String,
    Arc<MotionOutput>,
    watch::Receiver<bool>,
    MotionSender,
);

#[derive(Default)]
struct MotionOutput {
    pending: std::sync::Mutex<HashMap<(String, u32), ServerEvent>>,
    ready: Notify,
}
impl MotionOutput {
    fn send(&self, event: ServerEvent) {
        let ServerMessage::PoseChanged { update } = &event.event else {
            return;
        };
        self.pending
            .lock()
            .unwrap()
            .insert((update.match_id.clone(), update.actor_id.0), event);
        self.ready.notify_one();
    }
    fn clear(&self) {
        self.pending.lock().unwrap().clear();
    }
    async fn next(&self) -> ServerEvent {
        loop {
            let next = {
                let mut pending = self.pending.lock().unwrap();
                let key = pending
                    .iter()
                    .min_by_key(|(_, e)| match &e.event {
                        ServerMessage::PoseChanged { update } => update.event_seq,
                        _ => 0,
                    })
                    .map(|(key, _)| key.clone());
                key.and_then(|key| pending.remove(&key))
            };
            if let Some(next) = next {
                return next;
            }
            self.ready.notified().await;
        }
    }
}

async fn next_output(
    output: &mut Option<mpsc::Receiver<ServerEvent>>,
    motion: Option<&MotionOutput>,
) -> Option<ServerEvent> {
    if let Some(motion) = motion {
        Some(motion.next().await)
    } else {
        output.as_mut().unwrap().recv().await
    }
}

struct MotionSender {
    poses: watch::Sender<Option<ClientRequest>>,
    wake: watch::Receiver<Arc<Notify>>,
}
impl MotionSender {
    fn send(&self, request: ClientRequest) {
        self.poses.send_replace(Some(request));
        self.wake.borrow().notify_one();
    }
}

enum Command {
    Adopt {
        id: String,
        bundle: Box<rooms::Session>,
        request: ClientRequest,
        reply: oneshot::Sender<Option<rooms::Session>>,
    },
    Leave {
        id: String,
        request: ClientRequest,
        reply: oneshot::Sender<Option<rooms::Session>>,
    },
    Directory {
        rooms: Vec<RoomSnapshot>,
        command: Box<Command>,
    },
    Connected {
        id: String,
        motion_key: Option<String>,
        authorized: Option<GuestIdentity>,
        outgoing: mpsc::Sender<ServerEvent>,
        closing: watch::Sender<bool>,
        accepted: oneshot::Sender<()>,
    },
    AttachMotion {
        key: String,
        latest: watch::Receiver<Option<ClientRequest>>,
        wake: watch::Sender<Arc<Notify>>,
        outgoing: Arc<MotionOutput>,
        closing: watch::Sender<bool>,
        accepted: oneshot::Sender<Option<String>>,
    },
    Request {
        id: String,
        request: ClientRequest,
        queued_at: Instant,
    },
    Resume {
        key: String,
        generation: String,
        outgoing: mpsc::Sender<ServerEvent>,
        closing: watch::Sender<bool>,
        accepted: oneshot::Sender<Option<String>>,
    },
    Detached {
        id: String,
        generation: String,
        reason: &'static str,
    },
    MotionClosed {
        id: String,
        output: Arc<MotionOutput>,
    },
    Closed {
        id: String,
    },
}

async fn send_event(
    socket: &mut SplitSink<WebSocket, Message>,
    event: &ServerEvent,
    motion: bool,
) -> bool {
    if motion {
        let ServerMessage::PoseChanged { update } = &event.event else {
            return false;
        };
        let Some(bytes) = crate::motion_wire::encode_output(update) else {
            return false;
        };
        return matches!(
            tokio::time::timeout(
                Duration::from_secs(2),
                socket.send(Message::Binary(bytes.into()))
            )
            .await,
            Ok(Ok(()))
        );
    }
    let Ok(text) = serde_json::to_string(event) else {
        return false;
    };
    matches!(
        tokio::time::timeout(
            Duration::from_secs(2),
            socket.send(Message::Text(text.into()))
        )
        .await,
        Ok(Ok(()))
    )
}

async fn connection(
    socket: WebSocket,
    state: AppState,
    authorized: Option<GuestIdentity>,
    motion_key: Option<String>,
    motion: Option<MotionSocket>,
    resume: Option<ResumeSocket>,
) {
    let generation = resume.as_ref().map(|r| r.generation.clone());
    let is_motion = motion.is_some();
    let (id, mut output, closing_rx, replies, poses, motion_output) =
        if let Some((id, output, closing, poses)) = motion {
            (id, None, closing, None, Some(poses), Some(output))
        } else if let Some(resume) = resume {
            (
                resume.id,
                Some(resume.output),
                resume.closing_rx,
                Some(resume.outgoing),
                None,
                None,
            )
        } else {
            let id = uuid::Uuid::new_v4().to_string();
            let (outgoing, output) = mpsc::channel(OUTPUT_QUEUE_SIZE);
            let (closing, closing_rx) = watch::channel(false);
            let replies = outgoing.clone();
            let (accepted, acceptance) = oneshot::channel();
            if state
                .commands
                .try_send(Command::Connected {
                    id: id.clone(),
                    motion_key,
                    authorized,
                    outgoing,
                    closing,
                    accepted,
                })
                .is_err()
                || acceptance.await.is_err()
            {
                return;
            }
            (id, Some(output), closing_rx, Some(replies), None, None)
        };

    let generation = generation.unwrap_or_else(|| id.clone());
    let motion_cleanup = motion_output.clone();
    let (mut writer, mut reader) = socket.split();
    let writer_shutdown = state.shutdown.clone();
    let writer_closing = closing_rx.clone();
    let writer_task = tokio::spawn(async move {
        let global = wait_for_shutdown(writer_shutdown);
        let closing = wait_for_shutdown(writer_closing);
        tokio::pin!(global, closing);
        loop {
            tokio::select! {
                () = &mut global => break,
                () = &mut closing => break,
                event = next_output(&mut output, motion_output.as_deref()) => {
                    match event {
                        Some(event) if send_event(&mut writer, &event, is_motion).await => {},
                        _ => break,
                    }
                }
            }
        }
    });
    let mut writer_task = writer_task;
    let mut clock = tokio::time::interval(Duration::from_secs(1));
    clock.set_missed_tick_behavior(MissedTickBehavior::Skip);
    let mut last_input = Instant::now();
    let mut window = Instant::now();
    let mut count = 0;
    let global_shutdown = wait_for_shutdown(state.shutdown.clone());
    let close_connection = wait_for_shutdown(closing_rx);
    tokio::pin!(global_shutdown, close_connection);

    let mut reason = "socket closed";
    loop {
        tokio::select! {
            () = &mut global_shutdown => break,
            () = &mut close_connection => break,
            _ = clock.tick() => {
                if !is_motion && last_input.elapsed() >= Duration::from_secs(30) {
                    reason = "input timeout";
                    break;
                }
            }
            _ = &mut writer_task => break,
            incoming = reader.next() => {
                let Some(Ok(message)) = incoming else { break; };
                if window.elapsed() >= Duration::from_secs(1) {
                    window = Instant::now();
                    count = 0;
                }
                count += 1;
                if count > REQUESTS_PER_SECOND {
                    reason = "request rate exceeded";
                    break;
                }
                last_input = Instant::now();
                match message {
                    Message::Binary(bytes) if is_motion => {
                        let Some(request) = crate::motion_wire::decode_input(&bytes) else { break; };
                        poses.as_ref().unwrap().send(request);
                    }
                    Message::Text(text) if !is_motion => {
                        let request = serde_json::from_str::<ClientRequest>(&text);
                        match request {
                            Ok(request) => {
                                if is_motion && !matches!(request.command, ClientCommand::PoseInput { .. }) { break; }
                                if let Some(poses) = &poses {
                                    poses.send(request);
                                } else if state.commands.try_send(Command::Request {
                                    id: id.clone(), request, queued_at: Instant::now()
                                }).is_err() { break; }
                            }
                            Err(_) => {
                                let event = ServerEvent {
                                    protocol_version: VERSION,
                                    request_id: None,
                                    event: ServerMessage::Error { code: ErrorCode::BadRequest },
                                };
                                if replies.as_ref().is_none_or(|replies| replies.try_send(event).is_err()) { break; }
                            }
                        }
                    }
                    Message::Ping(_) | Message::Pong(_) => {},
                    Message::Close(_) | Message::Binary(_) | Message::Text(_) => break,
                }
            }
        }
    }
    writer_task.abort();
    // Cleanup must survive a temporarily full command queue.
    let command = if let Some(output) = motion_cleanup {
        Command::MotionClosed { id, output }
    } else {
        Command::Detached {
            id,
            generation,
            reason,
        }
    };
    let _ = state.commands.send(command).await;
}

struct Connection {
    authorized: Option<GuestIdentity>,
    account: Option<String>,
    outgoing: mpsc::Sender<ServerEvent>,
    closing: watch::Sender<bool>,
    connected_at_ms: u64,
    generation: String,
    detached_at: Option<u64>,
    clock: crate::clock_sync::ClockSync,
    timeline: crate::input_timeline::Stream,
    last_sequence: u64,
    cache: VecDeque<(ClientRequest, ServerEvent)>,
    pending_poses: HashMap<(String, u32), ServerEvent>,
}

struct MotionConnection {
    latest: watch::Receiver<Option<ClientRequest>>,
    wake: watch::Sender<Arc<Notify>>,
    outgoing: Arc<MotionOutput>,
    closing: watch::Sender<bool>,
    sequence: u64,
}

struct PendingContact {
    connection: String,
    target_connection: Option<String>,
    account: String,
    request: ClientRequest,
    contact_at: u64,
    wait_ms: u64,
    reported_at: u64,
    report_in_time: bool,
}

#[derive(Default)]
struct Coordinator {
    lobby: Lobby,
    connections: HashMap<String, Connection>,
    accounts: HashMap<String, String>,
    failed: Vec<String>,
    motion_keys: HashMap<String, String>,
    motion_connections: HashMap<String, MotionConnection>,
    pending_contacts: Vec<PendingContact>,
    motion_wake: Arc<Notify>,
}

impl Coordinator {
    async fn run(self, commands: mpsc::Receiver<Command>, shutdown: watch::Receiver<bool>) {
        rooms::Router::new(self).run(commands, shutdown).await;
    }

    async fn run_room(
        mut self,
        mut commands: mpsc::Receiver<Command>,
        shutdown: watch::Receiver<bool>,
        started: Instant,
        directory: watch::Sender<Option<RoomSnapshot>>,
    ) {
        let room_id = directory
            .borrow()
            .as_ref()
            .expect("room actor starts with a room")
            .room_id
            .clone();
        let mut tick = tokio::time::interval(Duration::from_millis(crate::limits::ROOM_TICK_MS));
        tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
        let stop = wait_for_shutdown(shutdown);
        tokio::pin!(stop);
        loop {
            tokio::select! {
                () = &mut stop => break,
                _ = tokio::time::sleep(Duration::from_millis(5)), if !self.pending_contacts.is_empty() => {
                    self.flush_contacts(started.elapsed().as_millis() as u64);
                }
                _ = self.motion_wake.notified() => {
                    self.poll_motion(started.elapsed().as_millis() as u64);
                }
                _ = tick.tick() => {
                    let now_ms = started.elapsed().as_millis() as u64;
                    self.poll_motion(now_ms);
                    self.advance(now_ms);
                    self.expire_detached(now_ms);
                    let unidentified: Vec<_> = self.connections.iter()
                        .filter(|(_, c)| c.account.is_none() && now_ms.saturating_sub(c.connected_at_ms) >= 10_000)
                        .map(|(id, _)| id.clone()).collect();
                    self.failed.extend(unidentified);
                }
                command = commands.recv() => {
                    let Some(command) = command else { break; };
                    let now_ms = started.elapsed().as_millis() as u64;
                    self.poll_motion(now_ms);
                    self.process(command, now_ms, commands.len());
                }
            }
            self.flush_contacts(started.elapsed().as_millis() as u64);
            while let Some(id) = self.failed.pop() {
                self.detach(
                    &id,
                    started.elapsed().as_millis() as u64,
                    "outgoing queue unavailable",
                );
            }
            let snapshot = self.lobby.snapshot(&room_id);
            directory.send_if_modified(|current| {
                if *current == snapshot {
                    false
                } else {
                    *current = snapshot;
                    true
                }
            });
            if self.connections.is_empty() {
                break;
            }
        }
        directory.send_replace(None);
        for connection in self.motion_connections.values() {
            connection.closing.send_replace(true);
        }
        for connection in self.connections.values() {
            connection.closing.send_replace(true);
        }
    }

    fn process(&mut self, command: Command, now_ms: u64, queue_depth: usize) {
        match command {
            Command::Directory { rooms, command } => {
                self.lobby.update_directory(rooms);
                self.process(*command, now_ms, queue_depth);
            }
            Command::Adopt {
                id,
                bundle,
                request,
                reply,
            } => {
                // An old socket may still be draining its disconnect after a reconnect.
                if bundle
                    .connection
                    .account
                    .as_ref()
                    .is_some_and(|a| self.accounts.contains_key(a))
                {
                    let mut session = *bundle;
                    let response = ServerEvent::reply(
                        request.request_id.clone(),
                        ServerMessage::Error {
                            code: ErrorCode::AccountConnected,
                        },
                    );
                    session.connection.last_sequence = session
                        .connection
                        .last_sequence
                        .max(request.sequence().unwrap_or(0));
                    session
                        .connection
                        .cache
                        .push_back((request, response.clone()));
                    if session.connection.cache.len() > CACHE_SIZE {
                        session.connection.cache.pop_front();
                    }
                    if session.connection.outgoing.try_send(response).is_err() {
                        session.connection.closing.send_replace(true);
                    }
                    if let Err(Some(session)) = reply.send(Some(session)) {
                        session.connection.closing.send_replace(true);
                    }
                    return;
                }
                self.install_session(&id, *bundle);
                self.request(&id, request, now_ms);
                let returned = self
                    .connections
                    .get(&id)
                    .and_then(|c| c.account.as_deref())
                    .is_none_or(|account| self.lobby.room_id_for(account).is_none());
                let session = returned.then(|| self.take_session(&id)).flatten();
                if let Err(Some(session)) = reply.send(session) {
                    session.connection.closing.send_replace(true);
                }
            }
            Command::Leave { id, request, reply } => {
                self.flush_contacts(now_ms);
                self.request(&id, request, now_ms);
                let left = self
                    .connections
                    .get(&id)
                    .and_then(|c| c.account.as_deref())
                    .is_none_or(|account| self.lobby.room_id_for(account).is_none());
                let session = left.then(|| self.take_session(&id)).flatten();
                if let Err(Some(session)) = reply.send(session) {
                    session.connection.closing.send_replace(true);
                }
            }
            Command::Connected {
                id,
                motion_key,
                authorized,
                outgoing,
                closing,
                accepted,
            } => {
                if let Some(key) = motion_key {
                    if self.motion_keys.contains_key(&key) {
                        return;
                    }
                    self.motion_keys.insert(key, id.clone());
                }
                self.connections.insert(
                    id.clone(),
                    Connection {
                        authorized,
                        account: None,
                        outgoing,
                        closing,
                        connected_at_ms: now_ms,
                        generation: id.clone(),
                        detached_at: None,
                        clock: crate::clock_sync::ClockSync::default(),
                        timeline: crate::input_timeline::Stream::default(),
                        last_sequence: 0,
                        cache: VecDeque::new(),
                        pending_poses: HashMap::new(),
                    },
                );
                if accepted.send(()).is_err() {
                    self.failed.push(id);
                }
            }
            Command::AttachMotion {
                key,
                latest,
                wake,
                outgoing,
                closing,
                accepted,
            } => {
                let id = self.motion_keys.get(&key).cloned().filter(|id| {
                    self.connections
                        .get(id)
                        .is_some_and(|c| c.detached_at.is_none())
                        && !self.motion_connections.contains_key(id)
                });
                if let Some(id) = &id {
                    wake.send_replace(self.motion_wake.clone());
                    self.motion_connections.insert(
                        id.clone(),
                        MotionConnection {
                            latest,
                            wake,
                            outgoing,
                            closing,
                            sequence: 0,
                        },
                    );
                }
                if accepted.send(id.clone()).is_err()
                    && let Some(id) = id
                {
                    self.failed.push(id);
                }
            }
            Command::Request {
                id,
                request,
                queued_at,
            } => {
                let queued_ms = queued_at.elapsed().as_millis();
                self.flush_contacts(now_ms);
                self.request(&id, request, now_ms);
                if queued_ms >= 50 {
                    tracing::debug!(queued_ms, queue_depth, "command queue delay");
                }
            }
            Command::Resume {
                key,
                generation,
                outgoing,
                closing,
                accepted,
            } => {
                let id = self.motion_keys.get(&key).cloned().filter(|id| {
                    self.connections.get(id).is_some_and(|c| {
                        c.account.is_some()
                            && c.detached_at.is_none_or(|at| {
                                now_ms.saturating_sub(at) < crate::limits::RECONNECT_GRACE_MS
                            })
                    })
                });
                if let Some(id) = &id {
                    if let Some(channel) = self.motion_connections.remove(id) {
                        channel.closing.send_replace(true);
                    }
                    let c = self.connections.get_mut(id).unwrap();
                    c.closing.send_replace(true);
                    c.closing = closing;
                    c.outgoing = outgoing;
                    c.generation = generation;
                    c.detached_at = None;
                    c.pending_poses.clear();
                    c.clock = crate::clock_sync::ClockSync::default();
                    c.timeline = crate::input_timeline::Stream::default();
                    let account = c.account.clone().unwrap();
                    let room = self
                        .lobby
                        .room_id_for(&account)
                        .and_then(|r| self.lobby.snapshot(r));
                    let recovery = self.lobby.recovery(&account, now_ms);
                    let profile = self.lobby.profile(&account).unwrap();
                    self.send(
                        id,
                        ServerEvent {
                            protocol_version: VERSION,
                            request_id: None,
                            event: ServerMessage::SessionResumed {
                                room,
                                profile,
                                recovery,
                            },
                        },
                    );
                    tracing::info!(connection = %id, "session resumed");
                }
                if let Err(Some(id)) = accepted.send(id) {
                    self.detach(&id, now_ms, "resume acceptance lost");
                }
            }
            Command::Detached {
                id,
                generation,
                reason,
            } => {
                if self
                    .connections
                    .get(&id)
                    .is_some_and(|c| c.generation == generation)
                {
                    self.detach(&id, now_ms, reason);
                }
            }
            Command::MotionClosed { id, output } => {
                if self
                    .motion_connections
                    .get(&id)
                    .is_some_and(|c| Arc::ptr_eq(&c.outgoing, &output))
                {
                    self.motion_connections.remove(&id);
                }
            }
            Command::Closed { id } => self.disconnect(&id),
        }
    }

    fn advance(&mut self, now_ms: u64) {
        self.flush_contacts(now_ms);
        let snapshots = self.lobby.advance(now_ms);
        self.broadcast_pve_in(None);
        self.broadcast_combat();
        self.broadcast_actions();
        self.broadcast_poses();
        for snapshot in snapshots {
            self.broadcast(snapshot);
        }
        self.flush_poses();
    }

    fn poll_motion(&mut self, now: u64) {
        let latest: Vec<_> = self
            .motion_connections
            .iter_mut()
            .filter_map(|(id, channel)| {
                if !channel.latest.has_changed().unwrap_or(false) {
                    return None;
                }
                channel
                    .latest
                    .borrow_and_update()
                    .clone()
                    .map(|request| (id.clone(), request))
            })
            .collect();
        for (id, request) in latest {
            self.motion_request(&id, request, now);
        }
    }

    fn motion_request(&mut self, id: &str, request: ClientRequest, now: u64) {
        let Some(channel) = self.motion_connections.get_mut(id) else {
            return;
        };
        let Ok(sequence) = request.sequence() else {
            return;
        };
        if request.protocol_version != VERSION
            || sequence <= channel.sequence
            || !matches!(request.command, ClientCommand::PoseInput { .. })
        {
            return;
        }
        channel.sequence = sequence;
        if request.timeline.is_some() && self.observe_timeline(id, &request, now, true).is_none() {
            return;
        }
        let _ = self.execute(id, &request.command, now);
    }

    fn observe_timeline(
        &mut self,
        id: &str,
        request: &ClientRequest,
        now: u64,
        motion: bool,
    ) -> Option<u64> {
        let Some(stamp) = &request.timeline else {
            return None;
        };
        let connection = self.connections.get_mut(id)?;
        let mapped = connection.clock.map(&stamp.epoch, stamp.sampled_ms, now);
        use crate::input_timeline::Source;
        let source_matches = matches!(
            (&request.command, stamp.source),
            (ClientCommand::ActionInput { .. }, Source::GatewayInput)
                | (ClientCommand::ContactInput { .. }, Source::NativeContact)
                | (ClientCommand::PoseInput { .. }, Source::NativePose)
                | (ClientCommand::ClockEcho(_), Source::Heartbeat)
                | (ClientCommand::CombatHeartbeat, Source::Heartbeat)
        );
        let sequence_valid = source_matches
            && mapped.is_some()
            && connection
                .timeline
                .observe_mapped(stamp, motion, mapped.unwrap().0);
        let (event_time, clamped) = mapped.unwrap_or((now, true));
        tracing::debug!(connection_id = id, combat_seq = stamp.combat_seq, sampled_ms = stamp.sampled_ms,
            gateway_ms = stamp.gateway_ms, received_ms = now, event_time,
            ?stamp.source, sequence_valid, clamped, motion, "input timeline observation");
        if !sequence_valid {
            tracing::warn!(connection_id = id, combat_seq = stamp.combat_seq,
                sampled_ms = stamp.sampled_ms, gateway_ms = stamp.gateway_ms,
                received_ms = now, source_matches, clock_mapped = mapped.is_some(),
                ?stamp.source, "input timeline rejected");
        }
        sequence_valid.then_some(event_time)
    }

    fn request(&mut self, id: &str, request: ClientRequest, now_ms: u64) {
        let Some(connection) = self.connections.get(id) else {
            return;
        };
        let sequence = match request.sequence() {
            Ok(sequence) => sequence,
            Err(code) => {
                self.send(
                    id,
                    ServerEvent::reply(request.request_id, ServerMessage::Error { code }),
                );
                return;
            }
        };
        // Pose snapshots are an overwrite stream, never a request/reply transaction.
        if matches!(&request.command, ClientCommand::PoseInput { .. }) {
            if request.protocol_version != VERSION || sequence <= connection.last_sequence {
                return;
            }
            if let Some(connection) = self.connections.get_mut(id) {
                connection.last_sequence = sequence;
            }
            if request.timeline.is_some()
                && self.observe_timeline(id, &request, now_ms, true).is_none()
            {
                return;
            }
            if let Err(code) = self.execute(id, &request.command, now_ms) {
                tracing::debug!(?code, "pose snapshot rejected");
            }
            return;
        }
        if let Some((original, reply)) = connection
            .cache
            .iter()
            .find(|(r, _)| r.request_id == request.request_id)
        {
            let reply = if original == &request {
                reply.clone()
            } else {
                ServerEvent::reply(
                    request.request_id,
                    ServerMessage::Error {
                        code: ErrorCode::RequestConflict,
                    },
                )
            };
            self.send(id, reply);
            return;
        }
        if let Some(pending) = self
            .pending_contacts
            .iter()
            .find(|p| p.connection == id && p.request.request_id == request.request_id)
        {
            if pending.request != request {
                self.send(
                    id,
                    ServerEvent::reply(
                        request.request_id,
                        ServerMessage::Error {
                            code: ErrorCode::RequestConflict,
                        },
                    ),
                );
            }
            return;
        }
        if sequence <= connection.last_sequence {
            self.send(
                id,
                ServerEvent::reply(
                    request.request_id,
                    ServerMessage::Error {
                        code: ErrorCode::StaleRequest,
                    },
                ),
            );
            return;
        }

        let account = connection.account.clone();
        let sampled_at = self.observe_timeline(id, &request, now_ms, false);
        if request.timeline.is_some() && sampled_at.is_none() {
            self.complete_request(
                id,
                request,
                ServerMessage::Error {
                    code: ErrorCode::BadRequest,
                },
            );
            return;
        }
        let room_id = account
            .as_deref()
            .and_then(|account| self.lobby.room_id_for(account))
            .map(str::to_owned);
        if let Some(account) = account {
            let changed = self.lobby.advance_account(&account, now_ms);
            if let Some(room_id) = room_id.as_deref() {
                self.broadcast_pve_in(Some(room_id));
                self.broadcast_combat_in(Some(room_id));
                self.broadcast_actions_in(Some(room_id));
                self.broadcast_poses_in(Some(room_id));
            }
            if let Some(snapshot) = changed {
                self.broadcast(snapshot);
            }
        }
        let (message, changed) = if request.protocol_version != VERSION {
            (
                ServerMessage::Error {
                    code: ErrorCode::UnsupportedVersion,
                },
                None,
            )
        } else {
            if matches!(request.command, ClientCommand::ContactInput { .. }) {
                let prepared = self.defer_contact(id, &request, now_ms);
                match prepared {
                    Ok(()) => return,
                    Err(code) => {
                        self.complete_request(id, request, ServerMessage::Error { code });
                        return;
                    }
                }
            }
            match self.execute_at(id, &request.command, now_ms, sampled_at) {
                Ok(result) => result,
                Err(code) => {
                    if let ClientCommand::CreateCustomRoom {
                        map_id, channel_id, ..
                    } = &request.command
                    {
                        tracing::warn!(map_id, channel_id, ?code, "custom room creation rejected");
                    }
                    (ServerMessage::Error { code }, None)
                }
            }
        };
        self.complete_request(id, request, message);
        if let Some(snapshot) = changed {
            self.broadcast(snapshot);
        }
        if let Some(room_id) = room_id.as_deref() {
            self.broadcast_pve_in(Some(room_id));
            self.broadcast_actions_in(Some(room_id));
            self.broadcast_combat_in(Some(room_id));
        }
    }

    fn complete_request(&mut self, id: &str, request: ClientRequest, message: ServerMessage) {
        let reply = ServerEvent::reply(request.request_id.clone(), message);
        if let Some(connection) = self.connections.get_mut(id) {
            connection.last_sequence = connection
                .last_sequence
                .max(request.sequence().unwrap_or(0));
            connection.cache.push_back((request, reply.clone()));
            if connection.cache.len() > CACHE_SIZE {
                connection.cache.pop_front();
            }
        }
        self.send(id, reply);
    }

    fn defer_contact(
        &mut self,
        id: &str,
        request: &ClientRequest,
        now: u64,
    ) -> Result<(), ErrorCode> {
        let ClientCommand::ContactInput {
            match_id,
            input_seq,
            contact,
            pose,
        } = &request.command
        else {
            return Err(ErrorCode::BadRequest);
        };
        // Bound memory and avoid accepting work whose eventual verdict cannot be retained.
        if self
            .pending_contacts
            .iter()
            .filter(|p| p.connection == id)
            .count()
            >= CACHE_SIZE
        {
            return Err(ErrorCode::BadRequest);
        }
        let account = self
            .connections
            .get(id)
            .and_then(|c| c.account.clone())
            .ok_or(ErrorCode::HelloRequired)?;
        self.apply_checkpoint(&account, match_id, pose.as_ref(), now)?;
        let (contact_at, target) = self
            .lobby
            .reserve_contact(&account, match_id, *input_seq, contact, now)?;
        let target_connection = target.and_then(|a| self.accounts.get(&a).cloned());
        let report_in_time = now.saturating_sub(contact_at) <= crate::clock_sync::MAX_REPORT_AGE_MS;
        let wait_ms =
            if !report_in_time || contact.target_actor_id == 0 || target_connection.is_none() {
                0
            } else {
                crate::adjudication::wait_window(
                    target_connection
                        .as_ref()
                        .and_then(|id| self.connections.get(id))
                        .and_then(|c| c.clock.one_way_p90(now)),
                )
            };
        self.connections.get_mut(id).unwrap().last_sequence = request.sequence()?;
        self.pending_contacts.push(PendingContact {
            connection: id.to_owned(),
            target_connection,
            account,
            request: request.clone(),
            contact_at,
            wait_ms,
            reported_at: now,
            report_in_time,
        });
        Ok(())
    }

    fn flush_contacts(&mut self, now: u64) {
        // Preserve event-time order per match: a later ready contact must not
        // stagger someone before an earlier pending contact has been decided.
        let mut pending = std::mem::take(&mut self.pending_contacts);
        pending.sort_by_key(|p| (p.contact_at, p.request.sequence().unwrap_or(0)));
        let mut blocked = std::collections::HashSet::new();
        for item in pending {
            let ClientCommand::ContactInput {
                match_id,
                input_seq,
                contact,
                ..
            } = &item.request.command
            else {
                unreachable!()
            };
            let ready = item
                .target_connection
                .as_ref()
                .and_then(|id| self.connections.get(id))
                .map_or(now >= item.contact_at.saturating_add(item.wait_ms), |c| {
                    c.timeline
                        .watermark
                        .readiness(item.contact_at, now, item.wait_ms)
                        != crate::adjudication::Readiness::Waiting
                });
            if !ready || blocked.contains(match_id) {
                blocked.insert(match_id.clone());
                self.pending_contacts.push(item);
                continue;
            }
            if !self.connections.contains_key(&item.connection) {
                continue;
            }
            let room_id = self.lobby.room_id_for(&item.account).map(str::to_owned);
            let result = if item.report_in_time {
                self.lobby.resolve_contact(
                    &item.account,
                    match_id,
                    contact.clone(),
                    now,
                    item.contact_at.saturating_add(item.wait_ms),
                )
            } else {
                Ok(false)
            };
            let message = match result {
                Ok(accepted) => ServerMessage::ContactAccepted {
                    match_id: match_id.clone(),
                    input_seq: *input_seq,
                    accepted,
                },
                Err(code) => ServerMessage::Error { code },
            };
            tracing::debug!(
                contact_at = item.contact_at,
                reported_at = item.reported_at,
                committed_at = now,
                wait_ms = item.wait_ms,
                "contact adjudicated"
            );
            self.complete_request(&item.connection, item.request, message);
            if let Some(room) = room_id.as_deref() {
                self.broadcast_actions_in(Some(room));
                self.broadcast_combat_in(Some(room));
            }
        }
    }

    fn execute(
        &mut self,
        id: &str,
        command: &ClientCommand,
        now_ms: u64,
    ) -> Result<(ServerMessage, Option<RoomSnapshot>), ErrorCode> {
        self.execute_at(id, command, now_ms, None)
    }

    fn execute_at(
        &mut self,
        id: &str,
        command: &ClientCommand,
        now_ms: u64,
        sampled_at: Option<u64>,
    ) -> Result<(ServerMessage, Option<RoomSnapshot>), ErrorCode> {
        let account = self.connections.get(id).and_then(|c| c.account.clone());
        let identity = match command {
            ClientCommand::Hello { account_id } => {
                Some((account_id, account_id.chars().take(31).collect::<String>()))
            }
            ClientCommand::GatewayHello {
                account_id,
                display_name,
            } => Some((account_id, display_name.clone())),
            _ => None,
        };
        if let Some((account_id, display_name)) = identity {
            if self
                .connections
                .get(id)
                .and_then(|c| c.authorized.as_ref())
                .is_some_and(|a| &a.account_id != account_id || a.display_name != display_name)
            {
                return Err(ErrorCode::InvalidAccountId);
            }
            if account.is_some() {
                return Err(ErrorCode::AlreadyIdentified);
            }
            if account_id.is_empty()
                || account_id.len() > 36
                || !account_id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
            {
                return Err(ErrorCode::InvalidAccountId);
            }
            if self.accounts.contains_key(account_id) {
                return Err(ErrorCode::AccountConnected);
            }
            let profile = self.lobby.identify(account_id, &display_name)?;
            if let Some(connection) = self.connections.get_mut(id) {
                connection.account = Some(account_id.clone());
            }
            self.accounts.insert(account_id.clone(), id.to_owned());
            return Ok((
                ServerMessage::Welcome {
                    account_id: account_id.clone(),
                    connection_id: id.to_owned(),
                    profile,
                },
                None,
            ));
        }
        let account = account.ok_or(ErrorCode::HelloRequired)?;
        let room = match command {
            ClientCommand::ClockProbe => {
                let connection = self
                    .connections
                    .get_mut(id)
                    .ok_or(ErrorCode::HelloRequired)?;
                let (epoch, nonce) = connection.clock.probe(now_ms);
                return Ok((
                    ServerMessage::ClockSample {
                        epoch,
                        nonce,
                        server_time_ms: now_ms,
                    },
                    None,
                ));
            }
            ClientCommand::ClockEcho(echo) => {
                let estimate = self
                    .connections
                    .get_mut(id)
                    .ok_or(ErrorCode::HelloRequired)?
                    .clock
                    .echo(echo, now_ms)
                    .ok_or(ErrorCode::BadRequest)?;
                tracing::debug!(
                    offset_ms = estimate.offset_ms,
                    uncertainty_ms = estimate.uncertainty_ms,
                    round_trip_ms = estimate.round_trip_ms,
                    "combat clock calibrated"
                );
                return Ok((ServerMessage::ClockReady(estimate), None));
            }
            ClientCommand::Ping | ClientCommand::CombatHeartbeat => {
                return Ok((
                    ServerMessage::Pong {
                        server_time_ms: now_ms,
                    },
                    None,
                ));
            }
            ClientCommand::ListRooms => {
                return Ok((
                    ServerMessage::RoomList {
                        rooms: self.lobby.list(),
                    },
                    None,
                ));
            }
            ClientCommand::ListChannels => {
                return Ok((
                    ServerMessage::ChannelList {
                        channels: self.lobby.channels(),
                    },
                    None,
                ));
            }
            ClientCommand::GetRoomListing {
                channel_id,
                room_number,
            } => {
                return Ok((
                    ServerMessage::RoomListing {
                        room: self.lobby.listing(*channel_id, *room_number)?,
                    },
                    None,
                ));
            }
            ClientCommand::ListRoomPage {
                channel_id,
                start,
                limit,
            } => {
                return Ok((
                    ServerMessage::RoomPage {
                        page: self.lobby.page(*channel_id, *start, *limit)?,
                    },
                    None,
                ));
            }
            ClientCommand::CreateCustomRoom {
                name,
                map_id,
                channel_id,
                password,
            } => self
                .lobby
                .create_custom(&account, name, *map_id, *channel_id, password)?,
            ClientCommand::JoinCustomRoom {
                room_guid,
                password,
            } => self.lobby.join_custom(&account, *room_guid, password)?,
            ClientCommand::CreatePveRoom { name, scenario_id } => {
                self.lobby.create_pve(&account, name, *scenario_id)?
            }
            ClientCommand::CreateRoom { name, map_id } => {
                self.lobby.create(&account, name, *map_id)?
            }
            ClientCommand::JoinRoom { room_id } => self.lobby.join(&account, room_id)?,
            ClientCommand::LeaveRoom => {
                let room_id = self.lobby.leave(&account, false)?;
                let changed = self.lobby.snapshot(&room_id);
                return Ok((ServerMessage::Left { room_id }, changed));
            }
            ClientCommand::SetCamp { camp } => self.lobby.set_camp(&account, *camp)?,
            ClientCommand::Ready { ready } => self.lobby.ready(&account, *ready)?,
            ClientCommand::Start => self.lobby.start(&account, now_ms)?,
            ClientCommand::SelectLoadout { match_id, loadout } => {
                self.lobby
                    .select_loadout(&account, match_id, loadout.clone())?
            }
            ClientCommand::Loaded { match_id } => self.lobby.loaded(&account, match_id, now_ms)?,
            ClientCommand::GetClientView { match_id } => {
                return Ok((
                    ServerMessage::ClientView {
                        view: self.lobby.client_view(&account, match_id)?,
                    },
                    None,
                ));
            }
            ClientCommand::ActionInput {
                match_id,
                input_seq,
                intent,
                pose,
            } => {
                self.apply_checkpoint(&account, match_id, pose.as_ref(), now_ms)?;
                self.lobby.action_input_at(
                    &account,
                    match_id,
                    *input_seq,
                    *intent,
                    sampled_at.unwrap_or(now_ms),
                    now_ms,
                )?;
                return Ok((
                    ServerMessage::ActionAccepted {
                        weapon_id: self.lobby.weapon(&account)?,
                        match_id: match_id.clone(),
                        input_seq: *input_seq,
                    },
                    None,
                ));
            }
            ClientCommand::ContactInput {
                match_id,
                input_seq,
                contact,
                pose,
            } => {
                self.apply_checkpoint(&account, match_id, pose.as_ref(), now_ms)?;
                let accepted = self.lobby.contact_input(
                    &account,
                    match_id,
                    *input_seq,
                    contact.clone(),
                    now_ms,
                )?;
                return Ok((
                    ServerMessage::ContactAccepted {
                        match_id: match_id.clone(),
                        input_seq: *input_seq,
                        accepted,
                    },
                    None,
                ));
            }
            ClientCommand::Respawn { match_id } => {
                self.lobby.respawn(&account, match_id, now_ms)?;
                return Ok((
                    ServerMessage::RespawnAccepted {
                        match_id: match_id.clone(),
                    },
                    None,
                ));
            }
            ClientCommand::PoseInput {
                match_id,
                input_seq,
                pose,
            } => {
                self.lobby
                    .pose_input(&account, match_id, *input_seq, pose.clone(), now_ms)?;
                return Ok((
                    ServerMessage::PoseAccepted {
                        match_id: match_id.clone(),
                        input_seq: *input_seq,
                    },
                    None,
                ));
            }
            ClientCommand::Hello { .. } | ClientCommand::GatewayHello { .. } => {
                unreachable!("hello is handled above")
            }
        };
        Ok((ServerMessage::Room { room: room.clone() }, Some(room)))
    }

    fn apply_checkpoint(
        &mut self,
        account: &str,
        match_id: &str,
        checkpoint: Option<&crate::motion::PoseCheckpoint>,
        now: u64,
    ) -> Result<(), ErrorCode> {
        let Some(checkpoint) = checkpoint else {
            return Ok(());
        };
        if checkpoint.input_seq == 0 {
            return Err(ErrorCode::InvalidPose);
        }
        match self.lobby.pose_input(
            account,
            match_id,
            checkpoint.input_seq,
            checkpoint.pose.clone(),
            now,
        ) {
            Ok(()) | Err(ErrorCode::StaleInput) => Ok(()),
            Err(error) => Err(error),
        }
    }

    fn send(&mut self, id: &str, event: ServerEvent) {
        let Some(connection) = self.connections.get_mut(id) else {
            return;
        };
        if connection.detached_at.is_some() {
            return;
        }
        if let ServerMessage::PoseChanged { update } = &event.event {
            if let Some(lane) = self.motion_connections.get(id) {
                lane.outgoing.send(event);
                return;
            }
            let key = (update.match_id.clone(), update.actor_id.0);
            // Once backlogged, keep subsequent poses in the same ordered batch.
            if !connection.pending_poses.is_empty() {
                connection.pending_poses.insert(key, event);
                return;
            }
            match connection.outgoing.try_send(event) {
                Ok(()) => {}
                Err(mpsc::error::TrySendError::Full(event)) => {
                    connection.pending_poses.insert(key, event);
                }
                Err(mpsc::error::TrySendError::Closed(_)) => self.failed.push(id.to_owned()),
            }
        } else {
            // A phase change invalidates unsent poses from the previous scene.
            if matches!(event.event, ServerMessage::RoomChanged { .. }) {
                connection.pending_poses.clear();
                if let Some(lane) = self.motion_connections.get(id) {
                    lane.outgoing.clear();
                }
            }
            if connection.outgoing.try_send(event).is_err() {
                self.failed.push(id.to_owned());
            }
        }
    }

    fn flush_poses(&mut self) {
        for (id, connection) in &mut self.connections {
            let mut pending: Vec<_> = connection.pending_poses.drain().collect();
            pending.sort_by_key(|(_, event)| match &event.event {
                ServerMessage::PoseChanged { update } => update.event_seq,
                _ => 0,
            });
            for (key, event) in pending {
                if let Some(lane) = self.motion_connections.get(id) {
                    lane.outgoing.send(event);
                    continue;
                }
                match connection.outgoing.try_send(event) {
                    Ok(()) => {}
                    Err(mpsc::error::TrySendError::Full(event)) => {
                        connection.pending_poses.insert(key, event);
                    }
                    Err(mpsc::error::TrySendError::Closed(_)) => {
                        self.failed.push(id.clone());
                        break;
                    }
                }
            }
        }
    }

    fn broadcast(&mut self, snapshot: RoomSnapshot) {
        for member in &snapshot.members {
            if let Some(id) = self.accounts.get(&member.account_id).cloned() {
                self.send(&id, ServerEvent::changed(snapshot.clone()));
            }
        }
    }

    fn detach(&mut self, id: &str, now: u64, reason: &str) {
        // Peers without a negotiated resume capability cannot recover this session.
        if !self.motion_keys.values().any(|owner| owner == id)
            || self.connections.get(id).is_none_or(|c| c.account.is_none())
        {
            self.disconnect(id);
            return;
        }
        if let Some(c) = self.connections.get_mut(id) {
            if c.detached_at.is_some() {
                return;
            }
            c.detached_at = Some(now);
            c.pending_poses.clear();
            c.closing.send_replace(true);
            tracing::warn!(connection = %id, reason, grace_ms = crate::limits::RECONNECT_GRACE_MS,
                "session transport detached; membership retained");
        }
        if let Some(channel) = self.motion_connections.remove(id) {
            channel.closing.send_replace(true);
        }
        if let Some(account) = self.connections.get(id).and_then(|c| c.account.clone()) {
            self.lobby.pause_input(&account, now);
            self.broadcast_actions();
        }
    }

    fn expire_detached(&mut self, now: u64) {
        let expired: Vec<_> = self
            .connections
            .iter()
            .filter(|(_, c)| {
                c.detached_at
                    .is_some_and(|at| now.saturating_sub(at) >= crate::limits::RECONNECT_GRACE_MS)
            })
            .map(|(id, _)| id.clone())
            .collect();
        for id in expired {
            tracing::info!(connection = %id, "reconnect grace expired");
            self.disconnect(&id);
        }
    }

    fn disconnect(&mut self, id: &str) {
        self.pending_contacts.retain(|p| p.connection != id);
        self.motion_keys.retain(|_, owner| owner != id);
        if let Some(channel) = self.motion_connections.remove(id) {
            channel.closing.send_replace(true);
        }
        if let Some(connection) = self.connections.remove(id) {
            connection.closing.send_replace(true);
            if let Some(account) = connection.account {
                self.accounts.remove(&account);
                if connection.authorized.is_some() {
                    self.lobby.forget_guest(&account);
                }
                if let Ok(room_id) = self.lobby.leave(&account, true)
                    && let Some(snapshot) = self.lobby.snapshot(&room_id)
                {
                    self.broadcast(snapshot);
                }
            }
        }
    }

    fn broadcast_pve_in(&mut self, room_id: Option<&str>) {
        let events = match room_id {
            Some(id) => self.lobby.take_pve_events_in(id),
            None => self.lobby.take_pve_events(),
        };
        for update in events {
            let recipients: Vec<_> = self
                .lobby
                .recipients(&update.room_id)
                .filter_map(|account| self.accounts.get(account).cloned())
                .collect();
            for id in recipients {
                self.send(
                    &id,
                    ServerEvent {
                        protocol_version: VERSION,
                        request_id: None,
                        event: ServerMessage::PveChanged {
                            update: update.clone(),
                        },
                    },
                );
            }
        }
    }

    fn broadcast_combat(&mut self) {
        self.broadcast_combat_in(None);
    }

    fn broadcast_combat_in(&mut self, room_id: Option<&str>) {
        let events = match room_id {
            Some(room_id) => self.lobby.take_combat_events_in(room_id),
            None => self.lobby.take_combat_events(),
        };
        for update in events {
            let recipients: Vec<_> = self
                .lobby
                .recipients(&update.room_id)
                .filter_map(|account| self.accounts.get(account).cloned())
                .collect();
            for id in recipients {
                self.send(
                    &id,
                    ServerEvent {
                        protocol_version: VERSION,
                        request_id: None,
                        event: ServerMessage::CombatChanged {
                            update: update.clone(),
                        },
                    },
                );
            }
        }
    }
    fn broadcast_actions(&mut self) {
        self.broadcast_actions_in(None);
    }

    fn broadcast_actions_in(&mut self, room_id: Option<&str>) {
        let events = match room_id {
            Some(room_id) => self.lobby.take_action_events_in(room_id),
            None => self.lobby.take_action_events(),
        };
        for action in events {
            let recipients: Vec<_> = self
                .lobby
                .recipients(&action.room_id)
                .filter_map(|account| self.accounts.get(account).cloned())
                .collect();
            for id in recipients {
                self.send(
                    &id,
                    ServerEvent {
                        protocol_version: VERSION,
                        request_id: None,
                        event: ServerMessage::ActionChanged {
                            action: action.clone(),
                        },
                    },
                );
            }
        }
    }

    fn broadcast_poses(&mut self) {
        self.broadcast_poses_in(None);
    }

    fn broadcast_poses_in(&mut self, room_id: Option<&str>) {
        let events = match room_id {
            Some(room_id) => self.lobby.take_pose_events_in(room_id),
            None => self.lobby.take_pose_events(),
        };
        for update in events {
            let recipients: Vec<_> = self
                .lobby
                .recipients(&update.room_id)
                .filter_map(|account| self.accounts.get(account).cloned())
                .collect();
            for id in recipients {
                self.send(
                    &id,
                    ServerEvent {
                        protocol_version: VERSION,
                        request_id: None,
                        event: ServerMessage::PoseChanged {
                            update: update.clone(),
                        },
                    },
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pending_scene() -> (
        Coordinator,
        String,
        mpsc::Receiver<ServerEvent>,
        mpsc::Receiver<ServerEvent>,
    ) {
        let mut c = Coordinator::default();
        let r = c.lobby.create("a", "combat", 1003).unwrap();
        c.lobby.join("b", &r.room_id).unwrap();
        c.lobby.ready("b", true).unwrap();
        let m = c
            .lobby
            .start("a", 0)
            .unwrap()
            .current_match
            .unwrap()
            .match_id;
        c.lobby.loaded("a", &m, 0).unwrap();
        c.lobby.loaded("b", &m, 0).unwrap();
        c.lobby.advance(3000);
        let mut receivers = Vec::new();
        for (account, x, heading) in [("a", 0., 0), ("b", 1.2, 180)] {
            let (outgoing, rx) = mpsc::channel(128);
            let (closing, _) = watch::channel(false);
            c.connections.insert(
                account.into(),
                Connection {
                    authorized: None,
                    account: Some(account.into()),
                    outgoing,
                    closing,
                    connected_at_ms: 0,
                    generation: String::new(),
                    detached_at: None,
                    clock: crate::clock_sync::ClockSync::default(),
                    timeline: crate::input_timeline::Stream::default(),
                    last_sequence: 0,
                    cache: VecDeque::new(),
                    pending_poses: HashMap::new(),
                },
            );
            c.accounts.insert(account.into(), account.into());
            receivers.push(rx);
            c.lobby
                .pose_input(
                    account,
                    &m,
                    1,
                    crate::motion::Pose {
                        position: [x, 0., 0.5],
                        heading,
                        forward_back: 0,
                        left_right: 0,
                        running: false,
                        motion: None,
                    },
                    3000,
                )
                .unwrap();
        }
        c.lobby
            .action_input("a", &m, 1, crate::battle::ActionIntent::AttackUp, 3000)
            .unwrap();
        c.lobby
            .action_input("a", &m, 2, crate::battle::ActionIntent::AttackRelease, 3001)
            .unwrap();
        c.lobby.advance(3360);
        c.lobby.take_action_events();
        c.lobby.take_pose_events();
        let b = receivers.pop().unwrap();
        (c, m, receivers.pop().unwrap(), b)
    }

    fn contact_request(m: &str, seq: u64, input_seq: u32, weapon: bool) -> ClientRequest {
        ClientRequest {
            protocol_version: VERSION,
            request_id: seq.to_string(),
            timeline: None,
            command: ClientCommand::ContactInput {
                match_id: m.into(),
                input_seq,
                pose: None,
                contact: crate::combat::ContactReport {
                    action_seq: 3,
                    action_id: 1,
                    state: 238,
                    elapsed_ms: 50,
                    target_actor_id: 2,
                    position: [0.9, 0., 1.5],
                    normal: [-1., 0., 0.],
                    direction: [1., 0., 0.],
                    material: 4,
                    weapon_contact: weapon,
                    timing: None,
                },
            },
        }
    }

    fn contact_outcomes(rx: &mut mpsc::Receiver<ServerEvent>) -> Vec<bool> {
        let mut outcomes = Vec::new();
        while let Ok(e) = rx.try_recv() {
            if let ServerMessage::CombatChanged { update } = e.event {
                if let crate::combat::CombatEffect::Contact { parried, .. } = update.effect {
                    outcomes.push(parried);
                }
            }
        }
        outcomes
    }

    #[test]
    fn leaving_with_a_pending_contact_finishes_its_reply_before_session_transfer() {
        let (mut c, m, mut a, mut b) = pending_scene();
        let report = contact_request(&m, 1, 1, false);
        c.request("a", report.clone(), 3415);
        assert_eq!(c.pending_contacts.len(), 1);
        c.request(
            "a",
            ClientRequest {
                protocol_version: VERSION,
                request_id: "2".into(),
                command: ClientCommand::LeaveRoom,
                timeline: None,
            },
            3416,
        );
        let session = c.take_session("a").unwrap();
        assert!(c.pending_contacts.is_empty());
        assert!(
            session
                .connection
                .cache
                .iter()
                .any(|(request, reply)| request == &report
                    && matches!(
                        reply.event,
                        ServerMessage::ContactAccepted {
                            accepted: false,
                            ..
                        }
                    ))
        );
        assert!(contact_outcomes(&mut a).is_empty());
        assert!(contact_outcomes(&mut b).is_empty());
    }

    #[test]
    fn queued_attack_earlier_than_heartbeat_does_not_poison_following_inputs() {
        let (mut c, m, _a, mut b) = pending_scene();
        let clock = &mut c.connections.get_mut("b").unwrap().clock;
        let (epoch, nonce) = clock.probe(3300);
        clock
            .echo(
                &crate::clock_sync::Echo {
                    epoch: epoch.clone(),
                    nonce,
                    received_ms: 3305,
                    sent_ms: 3305,
                },
                3310,
            )
            .unwrap();
        for (seq, sampled, command) in [
            (1, 3400, ClientCommand::CombatHeartbeat),
            (
                2,
                3399,
                ClientCommand::ActionInput {
                    match_id: m.clone(),
                    input_seq: 1,
                    intent: crate::battle::ActionIntent::AttackLeft,
                    pose: None,
                },
            ),
            (
                3,
                3402,
                ClientCommand::ActionInput {
                    match_id: m.clone(),
                    input_seq: 2,
                    intent: crate::battle::ActionIntent::AttackRelease,
                    pose: None,
                },
            ),
            (4, 3403, ClientCommand::CombatHeartbeat),
        ] {
            let source = if matches!(command, ClientCommand::CombatHeartbeat) {
                crate::input_timeline::Source::Heartbeat
            } else {
                crate::input_timeline::Source::GatewayInput
            };
            c.request(
                "b",
                ClientRequest {
                    protocol_version: VERSION,
                    request_id: seq.to_string(),
                    command,
                    timeline: Some(crate::input_timeline::Stamp {
                        epoch: epoch.clone(),
                        combat_seq: seq,
                        sampled_ms: sampled,
                        gateway_ms: 3400 + seq,
                        source,
                    }),
                },
                3410 + seq,
            );
        }
        let mut replies = Vec::new();
        while let Ok(event) = b.try_recv() {
            if let Some(id) = event.request_id {
                assert!(
                    matches!(
                        event.event,
                        ServerMessage::ActionAccepted { .. } | ServerMessage::Pong { .. }
                    ),
                    "unexpected reply {id}: {:?}",
                    event.event
                );
                replies.push(id);
            }
        }
        assert_eq!(replies, ["1", "2", "3", "4"]);
        assert_eq!(c.connections["b"].last_sequence, 4);
        assert!(c.failed.is_empty());
    }

    #[test]
    fn pending_contact_accepts_an_in_time_guard_that_arrives_after_report_and_commits_once() {
        let (mut c, m, mut a, mut b) = pending_scene();
        let request = contact_request(&m, 1, 1, true);
        c.request("a", request.clone(), 3415);
        c.request("a", request.clone(), 3416); // In-flight replay does not enqueue twice.
        assert_eq!(c.pending_contacts.len(), 1);
        c.flush_contacts(3420);
        assert!(a.try_recv().is_err());
        let clock = &mut c.connections.get_mut("b").unwrap().clock;
        let (epoch, nonce) = clock.probe(3300);
        clock
            .echo(
                &crate::clock_sync::Echo {
                    epoch: epoch.clone(),
                    nonce,
                    received_ms: 3305,
                    sent_ms: 3305,
                },
                3310,
            )
            .unwrap();
        let timeline = |seq, time, source| {
            Some(crate::input_timeline::Stamp {
                epoch: epoch.clone(),
                combat_seq: seq,
                sampled_ms: time,
                gateway_ms: time,
                source,
            })
        };
        c.request(
            "b",
            ClientRequest {
                protocol_version: VERSION,
                request_id: "1".into(),
                timeline: timeline(1, 3400, crate::input_timeline::Source::GatewayInput),
                command: ClientCommand::ActionInput {
                    match_id: m.clone(),
                    input_seq: 1,
                    intent: crate::battle::ActionIntent::BlockUp,
                    pose: None,
                },
            },
            3440,
        );
        c.request(
            "b",
            ClientRequest {
                protocol_version: VERSION,
                request_id: "2".into(),
                timeline: timeline(2, 3430, crate::input_timeline::Source::Heartbeat),
                command: ClientCommand::CombatHeartbeat,
            },
            3445,
        );
        c.flush_contacts(3445);
        assert!(c.pending_contacts.is_empty());
        assert_eq!(contact_outcomes(&mut a), [true]);
        assert_eq!(contact_outcomes(&mut b), [true]);
        c.request("a", request, 3441); // Completed replay returns only its cached reply.
        c.flush_contacts(3500);
        assert!(contact_outcomes(&mut a).is_empty());
        assert!(contact_outcomes(&mut b).is_empty());
    }

    #[test]
    fn deadline_commit_cannot_be_changed_by_a_later_backdated_guard() {
        let (mut c, m, mut a, mut b) = pending_scene();
        c.request("a", contact_request(&m, 1, 1, false), 3415);
        c.flush_contacts(3479);
        assert_eq!(c.pending_contacts.len(), 1);
        c.flush_contacts(3480);
        assert_eq!(contact_outcomes(&mut a), [false]);
        assert_eq!(contact_outcomes(&mut b), [false]);
        c.lobby
            .action_input_at("b", &m, 1, crate::battle::ActionIntent::BlockUp, 3400, 3490)
            .unwrap();
        c.flush_contacts(3500);
        assert!(contact_outcomes(&mut a).is_empty());
        assert!(contact_outcomes(&mut b).is_empty());
    }

    #[test]
    fn guard_release_and_earlier_body_contact_are_not_overridden_by_later_blade_contact() {
        let (mut c, m, mut a, mut b) = pending_scene();
        c.lobby
            .action_input_at("b", &m, 1, crate::battle::ActionIntent::BlockUp, 3380, 3385)
            .unwrap();
        c.lobby
            .action_input_at(
                "b",
                &m,
                2,
                crate::battle::ActionIntent::BlockRelease,
                3400,
                3430,
            )
            .unwrap();
        c.request("a", contact_request(&m, 1, 1, false), 3435);
        c.request("a", contact_request(&m, 2, 2, true), 3436);
        c.flush_contacts(3480);
        assert_eq!(contact_outcomes(&mut a), [false]);
        assert_eq!(contact_outcomes(&mut b), [false]);
        assert_eq!(c.connections["a"].last_sequence, 2);
    }

    #[test]
    fn movement_barrier_holds_pending_contact_until_combat_prefix_is_complete() {
        let (mut c, m, mut a, _) = pending_scene();
        let w = &mut c.connections.get_mut("b").unwrap().timeline.watermark;
        assert!(w.observe_combat(1, 3440));
        w.observe_barrier(2);
        c.request("a", contact_request(&m, 1, 1, false), 3445);
        c.flush_contacts(3446);
        assert_eq!(c.pending_contacts.len(), 1);
        assert!(a.try_recv().is_err());
        assert!(
            c.connections
                .get_mut("b")
                .unwrap()
                .timeline
                .watermark
                .observe_combat(2, 3450)
        );
        c.flush_contacts(3450);
        assert_eq!(contact_outcomes(&mut a), [false]);
    }

    #[test]
    fn guard_cutoff_is_independent_of_report_and_guard_arrival_order_within_caps() {
        for attacker_one_way in [5, 50] {
            for defender_one_way in [5, 50] {
                for guard_at in [3409, 3410, 3411] {
                    let (mut c, m, mut a, _b) = pending_scene();
                    let mut arrivals = [
                        (guard_at + defender_one_way, 0),
                        (3410 + attacker_one_way, 1),
                        (3480, 2),
                    ];
                    arrivals.sort_unstable();
                    for (now, kind) in arrivals {
                        c.flush_contacts(now);
                        match kind {
                            0 => c
                                .lobby
                                .action_input_at(
                                    "b",
                                    &m,
                                    1,
                                    crate::battle::ActionIntent::BlockUp,
                                    guard_at,
                                    now,
                                )
                                .unwrap(),
                            1 => c.request("a", contact_request(&m, 1, 1, true), now),
                            _ => {}
                        }
                        c.flush_contacts(now);
                    }
                    let expected = guard_at <= 3410;
                    assert_eq!(
                        contact_outcomes(&mut a),
                        if expected { vec![true] } else { vec![] },
                        "attacker={attacker_one_way} defender={defender_one_way} guard={guard_at}"
                    );
                    assert!(matches!(c.connections["a"].cache.back().unwrap().1.event,
                        ServerMessage::ContactAccepted { accepted, .. } if accepted == expected));
                }
            }
        }
    }

    #[test]
    fn report_lateness_is_bounded_at_receipt_not_extended_by_pending_wait() {
        let (mut c, m, mut a, _b) = pending_scene();
        c.request("a", contact_request(&m, 1, 1, false), 3511);
        c.flush_contacts(3511);
        assert!(contact_outcomes(&mut a).is_empty());
        assert!(matches!(
            c.connections["a"].cache.back().unwrap().1.event,
            ServerMessage::ContactAccepted {
                accepted: false,
                ..
            }
        ));
    }

    #[test]
    fn inputs_received_after_deadline_cannot_change_a_report_that_arrives_even_later() {
        for release in [false, true] {
            let (mut c, m, mut a, _b) = pending_scene();
            if release {
                c.lobby
                    .action_input_at("b", &m, 1, crate::battle::ActionIntent::BlockUp, 3380, 3385)
                    .unwrap();
            }
            c.lobby
                .action_input_at(
                    "b",
                    &m,
                    if release { 2 } else { 1 },
                    if release {
                        crate::battle::ActionIntent::BlockRelease
                    } else {
                        crate::battle::ActionIntent::BlockUp
                    },
                    3400,
                    3485,
                )
                .unwrap();
            // Report at +90ms is valid, but guard transport budget ended at +70ms.
            c.request("a", contact_request(&m, 1, 1, true), 3500);
            c.flush_contacts(3500);
            assert_eq!(
                contact_outcomes(&mut a),
                if release { vec![true] } else { vec![] }
            );
            assert!(c.pending_contacts.is_empty());
            if release {
                c.lobby.advance(3502);
                let resumed: Vec<_> = c
                    .lobby
                    .take_action_events()
                    .into_iter()
                    .filter(|e| e.actor_id.0 == 2)
                    .map(|e| e.state)
                    .collect();
                assert_eq!(resumed.last(), Some(&2));
            }
        }
    }

    fn pose_event(actor: u32, seq: u64) -> ServerEvent {
        ServerEvent {
            protocol_version: VERSION,
            request_id: None,
            event: ServerMessage::PoseChanged {
                update: crate::motion::PoseEvent {
                    room_id: "room".into(),
                    match_id: "match".into(),
                    actor_id: crate::client_view::ActorId(actor),
                    event_seq: seq,
                    input_seq: seq as u32,
                    server_time_ms: seq,
                    pose: crate::motion::Pose {
                        position: [0.; 3],
                        heading: 0,
                        forward_back: 0,
                        left_right: 0,
                        running: false,
                        motion: None,
                    },
                },
            },
        }
    }

    #[test]
    fn causal_checkpoint_is_bounded_and_duplicate_snapshots_do_not_overwrite_it() {
        let mut coordinator = Coordinator::default();
        let room = coordinator.lobby.create("a", "checkpoint", 1003).unwrap();
        let match_id = coordinator
            .lobby
            .start("a", 0)
            .unwrap()
            .current_match
            .unwrap()
            .match_id;
        coordinator.lobby.loaded("a", &match_id, 0).unwrap();
        coordinator.lobby.advance(3000);
        let checkpoint = crate::motion::PoseCheckpoint {
            input_seq: 1,
            pose: crate::motion::Pose {
                position: [0., 0., 0.5],
                heading: 0,
                forward_back: 0,
                left_right: 0,
                running: false,
                motion: None,
            },
        };
        coordinator
            .apply_checkpoint("a", &match_id, Some(&checkpoint), 3000)
            .unwrap();
        let mut invalid = checkpoint.clone();
        invalid.pose.position[0] = 1000.;
        // A repeated checkpoint cannot overwrite the already accepted snapshot.
        coordinator
            .apply_checkpoint("a", &match_id, Some(&invalid), 3001)
            .unwrap();
        invalid.input_seq = 2;
        assert_eq!(
            coordinator.apply_checkpoint("a", &match_id, Some(&invalid), 3001),
            Err(ErrorCode::InvalidPose)
        );
        coordinator.lobby.advance(3050);
        let events = coordinator.lobby.take_pose_events_in(&room.room_id);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].pose.position, checkpoint.pose.position);
    }

    #[test]
    fn pose_snapshots_never_allocate_a_reply_or_idempotency_cache_entry() {
        let mut coordinator = Coordinator::default();
        let (outgoing, mut output) = mpsc::channel(1);
        let (closing, _) = watch::channel(false);
        coordinator.connections.insert(
            "peer".into(),
            Connection {
                authorized: None,
                account: None,
                outgoing,
                closing,
                connected_at_ms: 0,
                generation: String::new(),
                detached_at: None,
                clock: crate::clock_sync::ClockSync::default(),
                timeline: crate::input_timeline::Stream::default(),
                last_sequence: 0,
                cache: VecDeque::new(),
                pending_poses: HashMap::new(),
            },
        );
        for sequence in 1..=100 {
            coordinator.request(
                "peer",
                ClientRequest {
                    timeline: None,
                    protocol_version: VERSION,
                    request_id: sequence.to_string(),
                    command: ClientCommand::PoseInput {
                        match_id: "match".into(),
                        input_seq: sequence as u32,
                        pose: crate::motion::Pose {
                            position: [0.; 3],
                            heading: 0,
                            forward_back: 0,
                            left_right: 0,
                            running: false,
                            motion: None,
                        },
                    },
                },
                sequence,
            );
        }
        assert!(output.try_recv().is_err());
        assert!(coordinator.connections["peer"].cache.is_empty());
        assert_eq!(coordinator.connections["peer"].last_sequence, 100);
        assert!(coordinator.failed.is_empty());
    }

    #[test]
    fn saturated_pose_output_keeps_latest_and_preserves_reliable_events() {
        let mut coordinator = Coordinator::default();
        let (outgoing, mut output) = mpsc::channel(1);
        let (closing, _) = watch::channel(false);
        coordinator.connections.insert(
            "peer".into(),
            Connection {
                authorized: None,
                account: None,
                outgoing,
                closing,
                connected_at_ms: 0,
                generation: String::new(),
                detached_at: None,
                clock: crate::clock_sync::ClockSync::default(),
                timeline: crate::input_timeline::Stream::default(),
                last_sequence: 0,
                cache: VecDeque::new(),
                pending_poses: HashMap::new(),
            },
        );
        coordinator.send("peer", pose_event(1, 1));
        for seq in 2..=100 {
            coordinator.send("peer", pose_event(1, seq));
        }
        assert!(coordinator.failed.is_empty());
        assert_eq!(coordinator.connections["peer"].pending_poses.len(), 1);
        output.try_recv().unwrap();
        let reliable = ServerEvent::reply(
            "1".into(),
            ServerMessage::Error {
                code: ErrorCode::BadRequest,
            },
        );
        coordinator.send("peer", reliable);
        coordinator.flush_poses();
        assert!(matches!(
            output.try_recv().unwrap().event,
            ServerMessage::Error { .. }
        ));
        coordinator.flush_poses();
        let ServerMessage::PoseChanged { update } = output.try_recv().unwrap().event else {
            panic!("expected pose");
        };
        assert_eq!(update.event_seq, 100);
        assert!(coordinator.connections["peer"].pending_poses.is_empty());

        coordinator.send("peer", pose_event(1, 101));
        coordinator.send("peer", pose_event(1, 102));
        output.try_recv().unwrap();
        coordinator.send("peer", pose_event(2, 103));
        assert!(output.try_recv().is_err());
        coordinator.flush_poses();
        let ServerMessage::PoseChanged { update } = output.try_recv().unwrap().event else {
            panic!("expected pose");
        };
        assert_eq!(update.event_seq, 102);
        coordinator.flush_poses();
        let ServerMessage::PoseChanged { update } = output.try_recv().unwrap().event else {
            panic!("expected pose");
        };
        assert_eq!(update.event_seq, 103);
    }

    #[test]
    fn slow_output_disconnects_and_releases_the_room_and_account() {
        let mut coordinator = Coordinator::default();
        let (outgoing, mut output) = mpsc::channel(1);
        let (closing, closed) = watch::channel(false);
        coordinator.connections.insert(
            "connection-a".into(),
            Connection {
                authorized: None,
                account: None,
                outgoing,
                closing,
                connected_at_ms: 0,
                generation: String::new(),
                detached_at: None,
                clock: crate::clock_sync::ClockSync::default(),
                timeline: crate::input_timeline::Stream::default(),
                last_sequence: 0,
                cache: VecDeque::new(),
                pending_poses: HashMap::new(),
            },
        );
        coordinator.request(
            "connection-a",
            ClientRequest {
                timeline: None,
                protocol_version: VERSION,
                request_id: "1".into(),
                command: ClientCommand::Hello {
                    account_id: "player-a".into(),
                },
            },
            0,
        );
        assert!(matches!(
            output.try_recv().unwrap().event,
            ServerMessage::Welcome { .. }
        ));
        coordinator.request(
            "connection-a",
            ClientRequest {
                timeline: None,
                protocol_version: VERSION,
                request_id: "2".into(),
                command: ClientCommand::CreateRoom {
                    name: "room".into(),
                    map_id: 1028,
                },
            },
            1,
        );
        // The reply occupies the only slot; the room notification cannot be
        // delivered. Closing the connection must also release its membership.
        assert!(!coordinator.failed.is_empty());
        while let Some(id) = coordinator.failed.pop() {
            coordinator.disconnect(&id);
        }
        assert!(*closed.borrow());
        assert!(coordinator.connections.is_empty());
        assert!(coordinator.accounts.is_empty());
        assert!(coordinator.lobby.list().is_empty());
        assert!(coordinator.lobby.create("player-a", "again", 1028).is_ok());
    }
    #[test]
    fn reconnect_grace_retains_membership_and_stale_socket_cannot_detach_replacement() {
        let mut c = Coordinator::default();
        let (outgoing, mut output) = mpsc::channel(128);
        let (closing, _) = watch::channel(false);
        let (accepted, _) = oneshot::channel();
        c.process(
            Command::Connected {
                id: "first".into(),
                motion_key: Some("ab".repeat(32)),
                authorized: None,
                outgoing,
                closing,
                accepted,
            },
            0,
            0,
        );
        c.failed.clear();
        let request = |seq: &str, command| ClientRequest {
            protocol_version: VERSION,
            request_id: seq.into(),
            command,
            timeline: None,
        };
        c.request(
            "first",
            request(
                "1",
                ClientCommand::Hello {
                    account_id: "player".into(),
                },
            ),
            1,
        );
        c.request(
            "first",
            request(
                "2",
                ClientCommand::CreateRoom {
                    name: "room".into(),
                    map_id: 1003,
                },
            ),
            2,
        );
        let room_id = c.lobby.room_id_for("player").unwrap().to_owned();
        let before = c.lobby.snapshot(&room_id).unwrap();
        while output.try_recv().is_ok() {}
        c.process(
            Command::Detached {
                id: "first".into(),
                generation: "first".into(),
                reason: "test",
            },
            10,
            0,
        );
        assert_eq!(c.lobby.snapshot(&room_id), Some(before.clone()));
        let (outgoing, mut resumed) = mpsc::channel(128);
        let (closing, _) = watch::channel(false);
        let (accepted, mut acceptance) = oneshot::channel();
        c.process(
            Command::Resume {
                key: "ab".repeat(32),
                generation: "second".into(),
                outgoing,
                closing,
                accepted,
            },
            20,
            0,
        );
        assert_eq!(acceptance.try_recv().unwrap().as_deref(), Some("first"));
        assert!(matches!(
            resumed.try_recv().unwrap().event,
            ServerMessage::SessionResumed { .. }
        ));
        c.process(
            Command::Detached {
                id: "first".into(),
                generation: "first".into(),
                reason: "stale",
            },
            30,
            0,
        );
        assert!(c.connections["first"].detached_at.is_none());
        c.process(
            Command::Detached {
                id: "first".into(),
                generation: "second".into(),
                reason: "test",
            },
            40,
            0,
        );
        c.detach("first", 50, "duplicate close");
        assert_eq!(c.connections["first"].detached_at, Some(40));
        c.expire_detached(40 + crate::limits::RECONNECT_GRACE_MS - 1);
        assert_eq!(c.lobby.snapshot(&room_id), Some(before));
        c.expire_detached(40 + crate::limits::RECONNECT_GRACE_MS);
        assert!(c.connections.is_empty() && c.accounts.is_empty() && c.motion_keys.is_empty());
        assert!(c.lobby.snapshot(&room_id).is_none());
    }
}

#[cfg(test)]
mod motion_output_tests {
    use super::*;
    fn pose(actor: u32, seq: u64) -> ServerEvent {
        ServerEvent {
            protocol_version: VERSION,
            request_id: None,
            event: ServerMessage::PoseChanged {
                update: crate::motion::PoseEvent {
                    room_id: uuid::Uuid::nil().to_string(),
                    match_id: uuid::Uuid::nil().to_string(),
                    actor_id: crate::client_view::ActorId(actor),
                    event_seq: seq,
                    input_seq: seq as u32,
                    server_time_ms: seq,
                    pose: crate::motion::Pose {
                        position: [0.; 3],
                        heading: 0,
                        forward_back: 0,
                        left_right: 0,
                        running: false,
                        motion: None,
                    },
                },
            },
        }
    }
    #[tokio::test]
    async fn slow_writer_keeps_only_newest_pose_per_actor_and_clears_old_scene() {
        let output = MotionOutput::default();
        output.send(pose(2, 1));
        for seq in 2..1000 {
            output.send(pose(1, seq));
        }
        assert_eq!(output.pending.lock().unwrap().len(), 2);
        for expected in [1, 999] {
            let event = output.next().await;
            let ServerMessage::PoseChanged { update } = event.event else {
                panic!()
            };
            assert_eq!(update.event_seq, expected);
        }
        output.send(pose(1, 1000));
        output.clear();
        assert!(
            tokio::time::timeout(Duration::from_millis(5), output.next())
                .await
                .is_err()
        );
        output.send(pose(1, 1001));
        assert!(
            tokio::time::timeout(Duration::from_millis(50), output.next())
                .await
                .is_ok()
        );
    }
}
