//! The router owns identities and directory snapshots; each room owns its combat loop.
use super::*;
use std::collections::HashSet;

pub(super) struct Session {
    pub connection: Connection,
    motion: Option<MotionConnection>,
    keys: Vec<String>,
    profile: crate::player::PlayerProfile,
}

impl Coordinator {
    pub(super) fn take_session(&mut self, id: &str) -> Option<Session> {
        let account = self.connections.get(id)?.account.clone()?;
        let profile = self.lobby.profile(&account)?;
        let pending = std::mem::take(&mut self.pending_contacts);
        for contact in pending {
            if contact.connection != id {
                self.pending_contacts.push(contact);
                continue;
            }
            if let ClientCommand::ContactInput {
                match_id,
                input_seq,
                ..
            } = &contact.request.command
            {
                let result = ServerMessage::ContactAccepted {
                    match_id: match_id.clone(),
                    input_seq: *input_seq,
                    accepted: false,
                };
                self.complete_request(id, contact.request, result);
            }
        }
        let connection = self.connections.remove(id)?;
        self.accounts.remove(&account);
        let keys = self
            .motion_keys
            .iter()
            .filter(|(_, owner)| owner.as_str() == id)
            .map(|(key, _)| key.clone())
            .collect();
        self.motion_keys.retain(|_, owner| owner != id);
        Some(Session {
            connection,
            motion: self.motion_connections.remove(id),
            keys,
            profile,
        })
    }

    pub(super) fn install_session(&mut self, id: &str, session: Session) {
        if let Some(account) = &session.connection.account {
            self.lobby.install_profile(account.clone(), session.profile);
            self.accounts.insert(account.clone(), id.to_owned());
        }
        for key in session.keys {
            self.motion_keys.insert(key, id.to_owned());
        }
        if let Some(motion) = session.motion {
            motion.wake.send_replace(self.motion_wake.clone());
            self.motion_connections.insert(id.to_owned(), motion);
            self.motion_wake.notify_one();
        }
        self.connections.insert(id.to_owned(), session.connection);
    }
}

struct Worker {
    commands: mpsc::Sender<Command>,
    directory: watch::Receiver<Option<RoomSnapshot>>,
    task: JoinHandle<()>,
}
struct Route {
    room: String,
    closing: watch::Sender<bool>,
}

struct Handoff {
    id: String,
    room: String,
    result: Result<Option<Session>, ()>,
}

pub(super) struct Router {
    lobby: Coordinator,
    workers: HashMap<String, Worker>,
    routes: HashMap<String, Route>,
    guests: HashSet<String>,
    handoffs: tokio::task::JoinSet<Handoff>,
    waiting: HashMap<String, VecDeque<Command>>,
    ready: VecDeque<Command>,
}
impl Router {
    pub fn new(lobby: Coordinator) -> Self {
        Self {
            lobby,
            workers: HashMap::new(),
            routes: HashMap::new(),
            guests: HashSet::new(),
            handoffs: tokio::task::JoinSet::new(),
            waiting: HashMap::new(),
            ready: VecDeque::new(),
        }
    }

    fn directory(&self) -> Vec<RoomSnapshot> {
        self.workers
            .values()
            .filter_map(|worker| worker.directory.borrow().clone())
            .collect()
    }

    fn forget_route(&mut self, id: &str) {
        if let Some(route) = self.routes.remove(id) {
            route.closing.send_replace(true);
        }
        if let Some(account) = self
            .lobby
            .accounts
            .iter()
            .find(|(_, owner)| owner.as_str() == id)
            .map(|(account, _)| account.clone())
        {
            self.lobby.accounts.remove(&account);
            if self.guests.remove(id) {
                self.lobby.lobby.forget_guest(&account);
            }
        }
        self.lobby.motion_keys.retain(|_, owner| owner != id);
    }

    fn forward(&mut self, room: &str, id: &str, command: Command) {
        let Some(worker) = self.workers.get(room) else {
            self.forget_route(id);
            return;
        };
        if worker.commands.try_send(command).is_err() {
            // Reliable inputs are never silently discarded. Only the overloaded peer closes.
            let sender = worker.commands.clone();
            let closed = Command::Closed { id: id.to_owned() };
            tokio::spawn(async move {
                let _ = sender.send(closed).await;
            });
            self.forget_route(id);
        }
    }

    fn spawn_room(&mut self, id: &str, started: Instant, shutdown: watch::Receiver<bool>) {
        let Some(account) = self
            .lobby
            .connections
            .get(id)
            .and_then(|c| c.account.clone())
        else {
            return;
        };
        let Some(room_id) = self.lobby.lobby.room_id_for(&account).map(str::to_owned) else {
            return;
        };
        let Some(room) = self.lobby.lobby.split_room(&room_id) else {
            return;
        };
        let Some(session) = self.lobby.take_session(id) else {
            return;
        };
        let route = Route {
            room: room_id.clone(),
            closing: session.connection.closing.clone(),
        };
        let keys = session.keys.clone();
        let mut coordinator = Coordinator {
            lobby: room,
            ..Coordinator::default()
        };
        coordinator.install_session(id, session);
        let (commands, receiver) = mpsc::channel(crate::limits::ROOM_COMMAND_QUEUE_SIZE);
        let (directory, snapshots) = watch::channel(coordinator.lobby.snapshot(&room_id));
        let task = tokio::spawn(coordinator.run_room(receiver, shutdown, started, directory));
        self.workers.insert(
            room_id,
            Worker {
                commands,
                directory: snapshots,
                task,
            },
        );
        self.routes.insert(id.to_owned(), route);
        // Global identity and attachment routing survive the ownership transfer.
        self.lobby.accounts.insert(account, id.to_owned());
        for key in keys {
            self.lobby.motion_keys.insert(key, id.to_owned());
        }
    }

    /// Only this session pauses during ownership transfer. Other room traffic keeps routing.
    fn join(&mut self, id: String, room: String, request: ClientRequest) {
        let Some(sender) = self.workers.get(&room).map(|w| w.commands.clone()) else {
            return;
        };
        let Some(session) = self.lobby.take_session(&id) else {
            return;
        };
        let account = session.connection.account.clone().unwrap();
        let closing = session.connection.closing.clone();
        for key in &session.keys {
            self.lobby.motion_keys.insert(key.clone(), id.clone());
        }
        self.lobby.accounts.insert(account, id.clone());
        self.routes.insert(
            id.clone(),
            Route {
                room: room.clone(),
                closing,
            },
        );
        self.waiting.insert(id.clone(), VecDeque::new());
        self.handoffs.spawn(async move {
            let (reply, received) = oneshot::channel();
            let command = Command::Adopt {
                id: id.clone(),
                bundle: Box::new(session),
                request,
                reply,
            };
            let result = if sender.send(command).await.is_err() {
                Err(())
            } else {
                received.await.map_err(|_| ())
            };
            Handoff { id, room, result }
        });
    }

    fn leave(&mut self, id: String, room: String, request: ClientRequest) {
        let Some(sender) = self.workers.get(&room).map(|w| w.commands.clone()) else {
            return;
        };
        self.waiting.insert(id.clone(), VecDeque::new());
        self.handoffs.spawn(async move {
            let (reply, received) = oneshot::channel();
            let command = Command::Leave {
                id: id.clone(),
                request,
                reply,
            };
            let result = if sender.send(command).await.is_err() {
                Err(())
            } else {
                received.await.map_err(|_| ())
            };
            Handoff { id, room, result }
        });
    }

    fn finish_handoff(&mut self, handoff: Handoff) {
        let Handoff { id, room, result } = handoff;
        let buffered = self.waiting.remove(&id).unwrap_or_default();
        // A disconnected/overloaded session cannot be resurrected by a late handoff.
        let closed = self.routes.get(&id).is_none_or(|r| *r.closing.borrow());
        match result {
            Ok(Some(session)) if !closed => {
                self.routes.remove(&id);
                self.lobby.install_session(&id, session);
                self.ready.extend(buffered);
            }
            Ok(None) if !closed => self.ready.extend(buffered),
            Ok(Some(session)) => {
                session.connection.closing.send_replace(true);
                self.forget_route(&id);
            }
            _ => {
                if let Some(worker) = self.workers.get(&room) {
                    let sender = worker.commands.clone();
                    let closed = Command::Closed { id: id.clone() };
                    tokio::spawn(async move {
                        let _ = sender.send(closed).await;
                    });
                }
                self.forget_route(&id);
            }
        }
    }

    fn buffer_handoff(&mut self, command: Command) -> Option<Command> {
        let id = match &command {
            Command::Request { id, .. } | Command::Closed { id } => Some(id.clone()),
            Command::AttachMotion { key, .. } => self.lobby.motion_keys.get(key).cloned(),
            _ => None,
        };
        let Some(id) = id else {
            return Some(command);
        };
        let Some(waiting) = self.waiting.get_mut(&id) else {
            return Some(command);
        };
        if waiting.len() < REQUESTS_PER_SECOND {
            waiting.push_back(command);
        } else {
            self.forget_route(&id);
        }
        None
    }

    pub async fn run(
        mut self,
        mut commands: mpsc::Receiver<Command>,
        shutdown: watch::Receiver<bool>,
    ) {
        let started = Instant::now();
        let mut tick = tokio::time::interval(Duration::from_millis(100));
        tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
        let stop = wait_for_shutdown(shutdown.clone());
        tokio::pin!(stop);
        loop {
            let command = if let Some(command) = self.ready.pop_front() {
                command
            } else {
                tokio::select! {
                    () = &mut stop => break,
                    completed = self.handoffs.join_next(), if !self.handoffs.is_empty() => {
                        if let Some(Ok(handoff)) = completed { self.finish_handoff(handoff); }
                        continue;
                    }
                    _ = tick.tick() => {
                        let finished: Vec<_> = self.workers.iter().filter(|(_, w)| w.task.is_finished())
                            .map(|(id, _)| id.clone()).collect();
                        for room in finished {
                            if let Some(worker) = self.workers.remove(&room) { let _ = worker.task.await; }
                            let members: Vec<_> = self.routes.iter().filter(|(id, r)| r.room == room && !self.waiting.contains_key(*id))
                                .map(|(id, _)| id.clone()).collect();
                            for id in members { self.forget_route(&id); }
                        }
                        let now = started.elapsed().as_millis() as u64;
                        let expired: Vec<_> = self.lobby.connections.iter().filter(|(_, c)|
                            c.account.is_none() && now.saturating_sub(c.connected_at_ms) >= 10_000)
                            .map(|(id, _)| id.clone()).collect();
                        for id in expired { self.lobby.disconnect(&id); self.guests.remove(&id); }
                        continue;
                    }
                    command = commands.recv() => { let Some(command) = command else { break; }; command }
                }
            };
            let Some(command) = self.buffer_handoff(command) else {
                continue;
            };
            let now = started.elapsed().as_millis() as u64;
            match command {
                Command::Connected {
                    ref id,
                    ref authorized,
                    ..
                } => {
                    if authorized.is_some() {
                        self.guests.insert(id.clone());
                    }
                    self.lobby.process(command, now, commands.len());
                }
                Command::AttachMotion { ref key, .. } => {
                    let route =
                        self.lobby.motion_keys.get(key).and_then(|id| {
                            self.routes.get(id).map(|r| (id.clone(), r.room.clone()))
                        });
                    if let Some((id, room)) = route {
                        self.forward(&room, &id, command);
                    } else {
                        self.lobby.process(command, now, commands.len());
                    }
                }
                Command::Request {
                    id,
                    request,
                    queued_at,
                } => {
                    if let Some(room) = self.routes.get(&id).map(|r| r.room.clone()) {
                        if matches!(request.command, ClientCommand::LeaveRoom) {
                            self.leave(id, room, request);
                        } else {
                            let list = matches!(
                                request.command,
                                ClientCommand::ListRooms
                                    | ClientCommand::ListRoomPage { .. }
                                    | ClientCommand::GetRoomListing { .. }
                            );
                            let command = Command::Request {
                                id: id.clone(),
                                request,
                                queued_at,
                            };
                            let command = if list {
                                Command::Directory {
                                    rooms: self.directory(),
                                    command: Box::new(command),
                                }
                            } else {
                                command
                            };
                            self.forward(&room, &id, command);
                        }
                    } else {
                        if matches!(
                            request.command,
                            ClientCommand::ListRooms
                                | ClientCommand::ListRoomPage { .. }
                                | ClientCommand::GetRoomListing { .. }
                                | ClientCommand::CreateRoom { .. }
                                | ClientCommand::CreateCustomRoom { .. }
                                | ClientCommand::CreatePveRoom { .. }
                        ) {
                            self.lobby.lobby.update_directory(self.directory());
                        }
                        let target = match &request.command {
                            ClientCommand::JoinRoom { room_id } => {
                                self.workers.contains_key(room_id).then(|| room_id.clone())
                            }
                            ClientCommand::JoinCustomRoom { room_guid, .. } => self
                                .directory()
                                .iter()
                                .find(|r| r.address.room_guid == *room_guid)
                                .map(|r| r.room_id.clone()),
                            _ => None,
                        };
                        if let Some(room) = target
                            && self
                                .lobby
                                .connections
                                .get(&id)
                                .is_some_and(|c| c.account.is_some())
                        {
                            self.join(id, room, request);
                            continue;
                        }
                        self.lobby.process(
                            Command::Request {
                                id: id.clone(),
                                request,
                                queued_at,
                            },
                            now,
                            commands.len(),
                        );
                        self.spawn_room(&id, started, shutdown.clone());
                    }
                }
                Command::Closed { ref id } => {
                    let id = id.clone();
                    if let Some(room) = self.routes.get(&id).map(|r| r.room.clone()) {
                        let closed = matches!(command, Command::Closed { .. });
                        self.forward(&room, &id, command);
                        if closed {
                            self.forget_route(&id);
                        }
                    } else {
                        let closed = matches!(command, Command::Closed { .. });
                        self.lobby.process(command, now, commands.len());
                        if closed {
                            self.guests.remove(&id);
                        }
                    }
                }
                _ => unreachable!("ownership commands are private to room actors"),
            }
            while let Some(id) = self.lobby.failed.pop() {
                self.lobby.disconnect(&id);
                self.guests.remove(&id);
            }
        }
        for route in self.routes.values() {
            route.closing.send_replace(true);
        }
        for connection in self.lobby.connections.values() {
            connection.closing.send_replace(true);
        }
        for (_, worker) in self.workers {
            drop(worker.commands);
            let _ = worker.task.await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::ClientCommand;

    fn request(seq: u64, command: ClientCommand) -> ClientRequest {
        ClientRequest {
            protocol_version: VERSION,
            request_id: seq.to_string(),
            command,
            timeline: None,
        }
    }
    fn identify(router: &mut Router, id: &str) -> mpsc::Receiver<ServerEvent> {
        let (outgoing, output) = mpsc::channel(128);
        let (closing, _) = watch::channel(false);
        let (accepted, _) = oneshot::channel();
        router.lobby.process(
            Command::Connected {
                id: id.to_owned(),
                motion_key: None,
                authorized: None,
                outgoing,
                closing,
                accepted,
            },
            0,
            0,
        );
        // The receiver intentionally belongs to the test rather than a WebSocket task.
        router.lobby.failed.clear();
        router.lobby.request(
            id,
            request(
                1,
                ClientCommand::Hello {
                    account_id: id.to_owned(),
                },
            ),
            0,
        );
        output
    }
    fn create(
        router: &mut Router,
        id: &str,
        started: Instant,
        shutdown: watch::Receiver<bool>,
    ) -> String {
        router.lobby.request(
            id,
            request(
                2,
                ClientCommand::CreateRoom {
                    name: id.to_owned(),
                    map_id: 1003,
                },
            ),
            0,
        );
        let room = router.lobby.lobby.room_id_for(id).unwrap().to_owned();
        router.spawn_room(id, started, shutdown);
        room
    }
    async fn pong(output: &mut mpsc::Receiver<ServerEvent>) {
        tokio::time::timeout(Duration::from_secs(2), async {
            while let Some(event) = output.recv().await {
                if matches!(event.event, ServerMessage::Pong { .. }) {
                    return;
                }
            }
            panic!("connection closed before pong");
        })
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn stalled_room_handoff_does_not_block_another_room_or_lose_identity() {
        let (stop, shutdown) = watch::channel(false);
        let started = Instant::now();
        let mut router = Router::new(Coordinator::default());
        let _a = identify(&mut router, "owner-a");
        let mut b = identify(&mut router, "owner-b");
        let mut guest = identify(&mut router, "guest");
        let before = router.lobby.lobby.profile("guest").unwrap();
        let room_a = create(&mut router, "owner-a", started, shutdown.clone());
        let room_b = create(&mut router, "owner-b", started, shutdown.clone());
        let sender = router.workers[&room_a].commands.clone();
        let permits = sender
            .try_reserve_many(crate::limits::ROOM_COMMAND_QUEUE_SIZE)
            .unwrap();
        router.join(
            "guest".to_owned(),
            room_a.clone(),
            request(
                2,
                ClientCommand::JoinRoom {
                    room_id: room_a.clone(),
                },
            ),
        );
        assert!(router.waiting.contains_key("guest"));
        router.forward(
            &room_b,
            "owner-b",
            Command::Request {
                id: "owner-b".to_owned(),
                request: request(3, ClientCommand::Ping),
                queued_at: Instant::now(),
            },
        );
        pong(&mut b).await;
        assert!(router.waiting.contains_key("guest"));
        // Global account reservation survives a join that has not entered the room yet.
        assert_eq!(
            router.lobby.accounts.get("guest").map(String::as_str),
            Some("guest")
        );
        drop(permits);
        let handoff = tokio::time::timeout(Duration::from_secs(2), router.handoffs.join_next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        router.finish_handoff(handoff);
        let joined = loop {
            let event = guest.recv().await.unwrap();
            if let ServerMessage::Room { room } = event.event {
                break room;
            }
        };
        assert_eq!(
            joined
                .members
                .iter()
                .find(|m| m.account_id == "guest")
                .unwrap()
                .profile,
            before
        );
        assert_ne!(
            router.workers[&room_a]
                .directory
                .borrow()
                .as_ref()
                .unwrap()
                .address
                .room_number,
            router.workers[&room_b]
                .directory
                .borrow()
                .as_ref()
                .unwrap()
                .address
                .room_number
        );
        router.leave(
            "guest".to_owned(),
            room_a,
            request(3, ClientCommand::LeaveRoom),
        );
        let handoff = tokio::time::timeout(Duration::from_secs(2), router.handoffs.join_next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        router.finish_handoff(handoff);
        assert!(router.lobby.connections.contains_key("guest"));
        assert_eq!(router.lobby.lobby.profile("guest"), Some(before));
        stop.send_replace(true);
        for (_, worker) in router.workers {
            worker.task.await.unwrap();
        }
    }

    #[tokio::test]
    async fn full_room_queue_closes_only_its_peer() {
        let (stop, shutdown) = watch::channel(false);
        let started = Instant::now();
        let mut router = Router::new(Coordinator::default());
        let _a = identify(&mut router, "owner-a");
        let mut b = identify(&mut router, "owner-b");
        let a = create(&mut router, "owner-a", started, shutdown.clone());
        let room_b = create(&mut router, "owner-b", started, shutdown.clone());
        let sender = router.workers[&a].commands.clone();
        let permits = sender
            .try_reserve_many(crate::limits::ROOM_COMMAND_QUEUE_SIZE)
            .unwrap();
        let closing = router.routes["owner-a"].closing.clone();
        router.forward(
            &a,
            "owner-a",
            Command::Request {
                id: "owner-a".to_owned(),
                request: request(3, ClientCommand::Ping),
                queued_at: Instant::now(),
            },
        );
        assert!(*closing.borrow());
        assert!(!*router.routes["owner-b"].closing.borrow());
        router.forward(
            &room_b,
            "owner-b",
            Command::Request {
                id: "owner-b".to_owned(),
                request: request(3, ClientCommand::Ping),
                queued_at: Instant::now(),
            },
        );
        pong(&mut b).await;
        drop(permits);
        stop.send_replace(true);
        for (_, worker) in router.workers {
            worker.task.await.unwrap();
        }
    }
}
