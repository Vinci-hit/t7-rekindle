use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use t7_game_server::maps::MapCatalog;
use t7_game_server::transport::{GameServer, wait_for_shutdown};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    task::JoinHandle,
};
use tokio_tungstenite::{
    MaybeTlsStream, WebSocketStream, connect_async,
    tungstenite::{Error, Message},
};

type Client = WebSocketStream<MaybeTlsStream<TcpStream>>;

#[tokio::test]
async fn melee_events_reach_both_members_once_with_server_owned_actor_identity() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    request(
        &mut a,
        1,
        json!({"type":"hello","payload":{"accountId":"action-owner"}}),
    )
    .await;
    request(
        &mut b,
        1,
        json!({"type":"hello","payload":{"accountId":"action-guest"}}),
    )
    .await;
    let room = request(
        &mut a,
        2,
        json!({"type":"createRoom","payload":{"name":"room","mapId":1003}}),
    )
    .await;
    request(
        &mut b,
        2,
        json!({"type":"joinRoom","payload":{"roomId":room["payload"]["room"]["roomId"]}}),
    )
    .await;
    request(&mut b, 3, json!({"type":"ready","payload":{"ready":true}})).await;
    let loading = request(&mut a, 3, json!({"type":"start"})).await;
    let id = loading["payload"]["room"]["currentMatch"]["matchId"].clone();
    let attack =
        json!({"type":"actionInput","payload":{"matchId":id,"inputSeq":1,"intent":"attackUp"}});
    assert_eq!(
        request(&mut a, 4, attack.clone()).await["payload"]["code"],
        "wrongPhase"
    );
    request(&mut a, 5, json!({"type":"loaded","payload":{"matchId":id}})).await;
    request(&mut b, 4, json!({"type":"loaded","payload":{"matchId":id}})).await;
    room_phase(&mut a, "running").await;
    room_phase(&mut b, "running").await;
    let accepted = request(&mut a, 6, attack.clone()).await;
    assert_eq!(accepted["type"], "actionAccepted");
    let local = receive(&mut a).await;
    let remote = receive(&mut b).await;
    assert_eq!(local, remote);
    assert_eq!(local["event"]["type"], "actionChanged");
    let action = &local["event"]["payload"]["action"];
    assert_eq!(action["actorId"], 1);
    assert_eq!(action["state"], 235);
    assert_eq!(action["eventSeq"], 1);
    assert_eq!(request(&mut a, 6, attack.clone()).await, accepted);
    assert_eq!(
        request(&mut a, 7, attack).await["payload"]["code"],
        "staleInput"
    );
    assert!(
        tokio::time::timeout(Duration::from_millis(80), b.next())
            .await
            .is_err()
    );
    let block =
        json!({"type":"actionInput","payload":{"matchId":id,"inputSeq":1,"intent":"blockRight"}});
    request(&mut b, 5, block).await;
    let remote = receive(&mut a).await;
    assert_eq!(remote["event"]["payload"]["action"]["actorId"], 2);
    assert_eq!(remote["event"]["payload"]["action"]["state"], 308);
    request(&mut a, 8, json!({"type":"leaveRoom"})).await;
    room_phase(&mut b, "waiting").await;
    request(&mut b, 6, json!({"type":"leaveRoom"})).await;
    a.close(None).await.unwrap();
    b.close(None).await.unwrap();
    server.stop().await;
}

#[tokio::test]
async fn solo_owner_can_start_without_ready_and_confirm_its_own_loading() {
    let server = TestServer::start(true).await;
    let mut client = server.connect().await;
    request(
        &mut client,
        1,
        json!({"type":"hello", "payload":{"accountId":"solo-owner"}}),
    )
    .await;
    request(
        &mut client,
        2,
        json!({"type":"createRoom", "payload":{"name":"solo", "mapId":1028}}),
    )
    .await;
    let loading = request(&mut client, 3, json!({"type":"start"})).await;
    assert_eq!(loading["payload"]["room"]["phase"], "loading");
    assert_eq!(loading["payload"]["room"]["members"][0]["ready"], false);
    let match_id = loading["payload"]["room"]["currentMatch"]["matchId"].clone();
    let view = request(
        &mut client,
        4,
        json!({"type":"getClientView", "payload":{"matchId":match_id}}),
    )
    .await;
    let actors = view["payload"]["view"]["actors"].as_array().unwrap();
    assert_eq!(actors.len(), 1);
    assert_eq!(actors[0]["localRid"], 1);
    let countdown = request(
        &mut client,
        5,
        json!({"type":"loaded", "payload":{"matchId":match_id}}),
    )
    .await;
    assert_eq!(countdown["payload"]["room"]["phase"], "countdown");
    request(&mut client, 6, json!({"type":"leaveRoom"})).await;
    let listed = request(&mut client, 7, json!({"type":"listRooms"})).await;
    assert!(listed["payload"]["rooms"].as_array().unwrap().is_empty());
    client.close(None).await.unwrap();
    server.stop().await;
}

#[tokio::test]
async fn configured_catalog_reaches_the_room_service_and_keeps_map_numbers() {
    let maps = MapCatalog::from_json(br#"{"mapIds":[7001,7002]}"#).unwrap();
    let server = TestServer::start_with_maps(true, maps).await;
    let mut a = server.connect().await;
    request(
        &mut a,
        1,
        json!({"type":"hello", "payload":{"accountId":"map-owner"}}),
    )
    .await;
    let created = request(
        &mut a,
        2,
        json!({"type":"createCustomRoom", "payload": {
        "mapId":7002, "name":"room", "channelId":1}}),
    )
    .await;
    assert_eq!(created["type"], "room");
    assert_eq!(created["payload"]["room"]["mapId"], 7002);
    let page = request(
        &mut a,
        3,
        json!({"type":"listRoomPage", "payload": {
        "channelId":1,"start":0,"limit":20}}),
    )
    .await;
    assert_eq!(page["payload"]["page"]["rooms"][0]["mapId"], 7002);
    request(&mut a, 4, json!({"type":"leaveRoom"})).await;
    let rejected = request(
        &mut a,
        5,
        json!({"type":"createCustomRoom", "payload": {
        "mapId":7003, "name":"room", "channelId":1}}),
    )
    .await;
    assert_eq!(rejected["payload"]["code"], "unsupportedMap");
    a.close(None).await.unwrap();
    server.stop().await;
}

#[tokio::test]
async fn gateway_player_ids_survive_room_changes_and_reidentification() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    let hello = |account: &str, name: &str| {
        json!({"type":"gatewayHello", "payload": {
        "accountId": account, "displayName": name}})
    };
    let welcome_a = request(&mut a, 1, hello("gateway-a", "玩家甲")).await;
    let welcome_b = request(&mut b, 1, hello("gateway-b", "玩家乙")).await;
    let user_a = welcome_a["payload"]["userId"].as_u64().unwrap();
    let user_b = welcome_b["payload"]["userId"].as_u64().unwrap();
    assert_ne!(user_a, user_b);
    let created = request(
        &mut a,
        2,
        json!({"type":"createCustomRoom", "payload": {
        "name":"room", "mapId":1028, "channelId":1}}),
    )
    .await;
    let guid = created["payload"]["room"]["roomGuid"].clone();
    let join = json!({"type":"joinCustomRoom", "payload": {"roomGuid":guid}});
    let joined = request(&mut b, 2, join.clone()).await;
    let members = joined["payload"]["room"]["members"].as_array().unwrap();
    assert_eq!(members[0]["userId"], user_a);
    assert_eq!(members[1]["userId"], user_b);
    assert_eq!(members[1]["displayName"], "玩家乙");
    // Drain the join notification before using the next update as a barrier.
    let _ = room_phase(&mut b, "waiting").await;
    request(&mut a, 3, json!({"type":"leaveRoom"})).await;
    let moved = room_phase(&mut b, "waiting").await;
    assert_eq!(moved["ownerAccountId"], "gateway-b");
    assert_eq!(moved["members"][0]["userId"], user_b);
    let rejoined = request(&mut a, 4, join).await;
    let member = rejoined["payload"]["room"]["members"]
        .as_array()
        .unwrap()
        .iter()
        .find(|member| member["accountId"] == "gateway-a")
        .unwrap();
    assert_eq!(member["userId"], user_a);
    assert_eq!(member["actorId"], 3);
    let _ = room_phase(&mut b, "waiting").await;
    a.close(None).await.unwrap();
    let _ = room_phase(&mut b, "waiting").await;
    let mut again = server.connect().await;
    let changed_name = request(&mut again, 1, hello("gateway-a", "different")).await;
    assert_eq!(changed_name["payload"]["code"], "invalidDisplayName");
    let identified = request(&mut again, 2, hello("gateway-a", "玩家甲")).await;
    assert_eq!(identified["payload"]["userId"], user_a);
    again.close(None).await.unwrap();
    b.close(None).await.unwrap();
    server.stop().await;
}

struct TestServer {
    service: GameServer,
    address: std::net::SocketAddr,
    task: JoinHandle<std::io::Result<()>>,
}

impl TestServer {
    async fn start(dev_lobby: bool) -> Self {
        Self::start_with_maps(dev_lobby, MapCatalog::default()).await
    }

    async fn start_with_maps(dev_lobby: bool, maps: MapCatalog) -> Self {
        Self::start_service(GameServer::with_maps(dev_lobby, maps)).await
    }

    async fn start_service(service: GameServer) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let router = service.router();
        let shutdown = service.shutdown_receiver();
        let task = tokio::spawn(async move {
            axum::serve(
                listener,
                router.into_make_service_with_connect_info::<std::net::SocketAddr>(),
            )
            .with_graceful_shutdown(wait_for_shutdown(shutdown))
            .await
        });
        Self {
            service,
            address,
            task,
        }
    }

    async fn connect(&self) -> Client {
        connect_async(format!("ws://{}/ws", self.address))
            .await
            .unwrap()
            .0
    }

    async fn stop(self) {
        self.service.shutdown();
        tokio::time::timeout(Duration::from_secs(5), self.task)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        self.service.stopped().await.unwrap();
    }
}

async fn receive(client: &mut Client) -> Value {
    loop {
        let message = tokio::time::timeout(Duration::from_secs(5), client.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        if let Message::Text(text) = message {
            return serde_json::from_str(&text).unwrap();
        }
    }
}

async fn request(client: &mut Client, id: u64, command: Value) -> Value {
    let request_id = id.to_string();
    client
        .send(Message::Text(
            json!({
                "protocolVersion": 1, "requestId": id.to_string(), "command": command
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    loop {
        let response = receive(client).await;
        if response["requestId"].as_str() == Some(request_id.as_str()) {
            return response["event"].clone();
        }
    }
}

async fn room_phase(client: &mut Client, phase: &str) -> Value {
    loop {
        let message = receive(client).await;
        if message["event"]["type"] == "roomChanged"
            && message["event"]["payload"]["room"]["phase"] == phase
        {
            return message["event"]["payload"]["room"].clone();
        }
    }
}

async fn get(server: &TestServer, path: &str) -> String {
    let mut http = TcpStream::connect(server.address).await.unwrap();
    http.write_all(
        format!(
            "GET {path} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
            server.address
        )
        .as_bytes(),
    )
    .await
    .unwrap();
    let mut response = String::new();
    tokio::time::timeout(Duration::from_secs(5), http.read_to_string(&mut response))
        .await
        .unwrap()
        .unwrap();
    response
}

#[tokio::test]
async fn custom_directory_supports_discovery_join_replay_and_cleanup_on_one_connection() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    for (client, account) in [(&mut a, "player-a"), (&mut b, "player-b")] {
        request(
            client,
            1,
            json!({"type":"hello","payload":{"accountId":account}}),
        )
        .await;
    }
    let create =
        json!({"type":"createCustomRoom","payload":{"name":"custom","mapId":1028,"channelId":1}});
    let created = request(&mut a, 2, create.clone()).await;
    assert_eq!(created["type"], "room");
    let room = &created["payload"]["room"];
    let guid = room["roomGuid"].as_str().unwrap();
    let uuid = room["roomId"].as_str().unwrap();
    assert_eq!(room["phase"], "waiting");
    assert!(room["currentMatch"].is_null());
    assert_eq!(request(&mut a, 2, create).await, created);
    let channels = request(&mut b, 2, json!({"type":"listChannels"})).await;
    assert_eq!(channels["payload"]["channels"].as_array().unwrap().len(), 1);
    assert_eq!(channels["payload"]["channels"][0]["channelId"], 1);
    let page_query = json!({"type":"listRoomPage","payload":{"channelId":1,"start":0,"limit":20}});
    let page = request(&mut b, 3, page_query.clone()).await;
    assert_eq!(page["type"], "roomPage");
    assert_eq!(page["payload"]["page"]["total"], 1);
    let listed = &page["payload"]["page"]["rooms"][0];
    assert_eq!(listed["roomGuid"], guid);
    assert_eq!(listed["roomId"], uuid);
    assert!(listed.get("members").is_none());
    let join = json!({"type":"joinCustomRoom","payload":{"roomGuid":guid}});
    let joined = request(&mut b, 4, join.clone()).await;
    assert_eq!(joined["payload"]["room"]["roomId"], uuid);
    assert_eq!(joined["payload"]["room"]["roomGuid"], guid);
    assert_eq!(
        joined["payload"]["room"]["members"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert_eq!(request(&mut b, 4, join.clone()).await, joined);
    request(&mut a, 3, json!({"type":"ready","payload":{"ready":true}})).await;
    request(&mut b, 5, json!({"type":"ready","payload":{"ready":true}})).await;
    let loading = request(&mut a, 4, json!({"type":"start"})).await;
    assert_eq!(loading["payload"]["room"]["phase"], "loading");
    assert_eq!(loading["payload"]["room"]["roomGuid"], guid);
    let page = request(&mut b, 6, page_query.clone()).await;
    assert_eq!(page["payload"]["page"]["rooms"][0]["phase"], "loading");
    assert_eq!(page["payload"]["page"]["rooms"][0]["joinable"], false);
    request(&mut b, 7, json!({"type":"leaveRoom"})).await;
    request(&mut a, 5, json!({"type":"leaveRoom"})).await;
    assert_eq!(
        request(&mut b, 8, join).await["payload"]["code"],
        "roomNotFound"
    );
    assert_eq!(
        request(&mut b, 9, page_query.clone()).await["payload"]["page"]["total"],
        0
    );
    let replacement = request(
        &mut a,
        6,
        json!({"type":"createRoom","payload":{"name":"dev","mapId":1028}}),
    )
    .await;
    assert_ne!(replacement["payload"]["room"]["roomGuid"], guid);
    let page = request(&mut b, 10, page_query).await;
    assert_eq!(
        page["payload"]["page"]["rooms"][0]["roomId"],
        replacement["payload"]["room"]["roomId"]
    );
    let queried = request(&mut b, 11, json!({"type":"getRoomListing","payload":{"channelId":1,"roomNumber":replacement["payload"]["room"]["roomNumber"]}})).await;
    assert_eq!(queried["type"], "roomListing");
    assert_eq!(
        queried["payload"]["room"],
        page["payload"]["page"]["rooms"][0]
    );
    assert_eq!(request(&mut b, 12, json!({"type":"getRoomListing","payload":{"channelId":1,"roomNumber":room["roomNumber"]}})).await["payload"]["code"], "roomNotFound");
    assert_eq!(request(&mut b, 13, json!({"type":"getRoomListing","payload":{"channelId":2,"roomNumber":replacement["payload"]["room"]["roomNumber"]}})).await["payload"]["code"], "unsupportedChannel");
    server.stop().await;
}

#[tokio::test]
async fn custom_directory_uses_normal_admission_and_rejects_unsupported_options() {
    let server = TestServer::start(true).await;
    let mut client = server.connect().await;
    for (i, command) in [
        json!({"type":"listChannels"}),
        json!({"type":"listRoomPage","payload":{"channelId":1,"start":0,"limit":1}}),
        json!({"type":"createCustomRoom","payload":{"name":"room","mapId":1028,"channelId":1}}),
        json!({"type":"joinCustomRoom","payload":{"roomGuid":"1"}}),
    ]
    .into_iter()
    .enumerate()
    {
        assert_eq!(
            request(&mut client, i as u64 + 1, command).await["payload"]["code"],
            "helloRequired"
        );
    }
    request(
        &mut client,
        5,
        json!({"type":"hello","payload":{"accountId":"player-a"}}),
    )
    .await;
    assert_eq!(
        request(
            &mut client,
            6,
            json!({"type":"createCustomRoom","payload":{"name":"room","mapId":1028,"channelId":2}})
        )
        .await["payload"]["code"],
        "unsupportedChannel"
    );
    assert_eq!(request(&mut client, 7, json!({"type":"createCustomRoom","payload":{"name":"room","mapId":1028,"channelId":1,"password":"secret"}})).await["payload"]["code"], "passwordUnsupported");
    assert_eq!(
        request(
            &mut client,
            8,
            json!({"type":"listRoomPage","payload":{"channelId":1,"start":0,"limit":0}})
        )
        .await["payload"]["code"],
        "invalidRoomPage"
    );
    let created = request(
        &mut client,
        9,
        json!({"type":"createCustomRoom","payload":{"name":"room","mapId":1028,"channelId":1}}),
    )
    .await;
    assert_eq!(created["payload"]["room"]["roomNumber"], 1);
    assert_eq!(
        created["payload"]["room"]["members"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    server.stop().await;
}

#[tokio::test]
async fn client_views_follow_identified_connection_membership_and_match() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    for (client, account) in [(&mut a, "player-a"), (&mut b, "player-b")] {
        request(
            client,
            1,
            json!({"type":"hello","payload":{"accountId":account}}),
        )
        .await;
    }
    let created = request(
        &mut a,
        2,
        json!({"type":"createRoom","payload":{"name":"room","mapId":1028}}),
    )
    .await;
    let id = created["payload"]["room"]["roomId"].as_str().unwrap();
    assert_eq!(
        request(
            &mut a,
            3,
            json!({"type":"getClientView","payload":{"matchId":"none"}})
        )
        .await["payload"]["code"],
        "wrongPhase"
    );
    request(
        &mut b,
        2,
        json!({"type":"joinRoom","payload":{"roomId":id}}),
    )
    .await;
    request(&mut a, 4, json!({"type":"ready","payload":{"ready":true}})).await;
    request(&mut b, 3, json!({"type":"ready","payload":{"ready":true}})).await;
    let loading = request(&mut a, 5, json!({"type":"start"})).await;
    let match_id = loading["payload"]["room"]["currentMatch"]["matchId"]
        .as_str()
        .unwrap();
    let query = json!({"type":"getClientView","payload":{"matchId":match_id}});
    let a_view = request(&mut a, 6, query.clone()).await;
    let b_view = request(&mut b, 4, query.clone()).await;
    assert_eq!(a_view["type"], "clientView");
    assert_eq!(a_view["payload"]["view"]["localActorId"], 1);
    assert_eq!(b_view["payload"]["view"]["localActorId"], 2);
    assert_eq!(a_view["payload"]["view"]["actors"][0]["localRid"], 1);
    assert_eq!(b_view["payload"]["view"]["actors"][0]["localRid"], 1);
    assert_eq!(b_view["payload"]["view"]["actors"][1]["actorId"], 1);
    assert_eq!(
        request(
            &mut b,
            5,
            json!({"type":"getClientView","payload":{"matchId":"other"}})
        )
        .await["payload"]["code"],
        "staleMatch"
    );
    let mut outsider = server.connect().await;
    request(
        &mut outsider,
        1,
        json!({"type":"hello","payload":{"accountId":"outsider"}}),
    )
    .await;
    assert_eq!(
        request(&mut outsider, 2, query.clone()).await["payload"]["code"],
        "notInRoom"
    );
    request(&mut b, 6, json!({"type":"leaveRoom"})).await;
    assert_eq!(
        request(&mut a, 7, query.clone()).await["payload"]["code"],
        "wrongPhase"
    );
    assert_eq!(
        request(&mut b, 7, query).await["payload"]["code"],
        "notInRoom"
    );
    server.stop().await;
}

#[tokio::test]
async fn development_page_is_opt_in_and_browser_origins_are_checked() {
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    let disabled = TestServer::start(false).await;
    for path in ["/dev", "/dev/lobby.js", "/dev/lobby.css"] {
        assert!(get(&disabled, path).await.starts_with("HTTP/1.1 404"));
    }
    disabled.stop().await;

    let enabled = TestServer::start(true).await;
    let page = get(&enabled, "/dev").await;
    assert!(page.starts_with("HTTP/1.1 200"));
    assert!(page.contains("content-security-policy:"));
    assert!(page.contains("cache-control: no-store"));
    assert!(page.contains("text/html; charset=utf-8"));
    for (path, mime) in [
        ("/dev/lobby.js", "text/javascript"),
        ("/dev/lobby.css", "text/css"),
    ] {
        let asset = get(&enabled, path).await;
        assert!(asset.starts_with("HTTP/1.1 200"));
        assert!(asset.contains(mime));
    }
    let mut allowed = format!("ws://{}/ws", enabled.address)
        .into_client_request()
        .unwrap();
    allowed.headers_mut().insert(
        "origin",
        format!("http://{}", enabled.address).parse().unwrap(),
    );
    let (mut socket, _) = connect_async(allowed).await.unwrap();
    socket.close(None).await.unwrap();
    for origin in ["https://example.invalid", "null"] {
        let mut denied = format!("ws://{}/ws", enabled.address)
            .into_client_request()
            .unwrap();
        denied
            .headers_mut()
            .insert("origin", origin.parse().unwrap());
        assert!(
            matches!(connect_async(denied).await, Err(Error::Http(response)) if response.status().as_u16() == 403)
        );
    }
    enabled.stop().await;
}

#[tokio::test]
async fn two_connections_share_a_room_and_disconnect_aborts_the_match() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    assert_eq!(
        request(
            &mut a,
            1,
            json!({"type":"hello","payload":{"accountId":"player-a"}})
        )
        .await["type"],
        "welcome"
    );
    assert_eq!(
        request(
            &mut b,
            1,
            json!({"type":"hello","payload":{"accountId":"player-b"}})
        )
        .await["type"],
        "welcome"
    );
    let create = json!({"type":"createRoom","payload":{"name":"测试房间","mapId":1028}});
    let original = request(&mut a, 2, create.clone()).await;
    let id = original["payload"]["room"]["roomId"]
        .as_str()
        .unwrap()
        .to_owned();
    let joined = request(
        &mut b,
        2,
        json!({"type":"joinRoom","payload":{"roomId":id}}),
    )
    .await;
    assert_eq!(
        joined["payload"]["room"]["members"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert_eq!(request(&mut a, 2, create).await, original);
    assert_eq!(
        request(&mut a, 2, json!({"type":"leaveRoom"})).await["payload"]["code"],
        "requestConflict"
    );
    let list = request(&mut a, 3, json!({"type":"listRooms"})).await;
    assert_eq!(list["payload"]["rooms"].as_array().unwrap().len(), 1);
    assert_eq!(
        request(&mut a, 4, json!({"type":"start"})).await["payload"]["code"],
        "notReady"
    );
    assert_eq!(
        request(&mut b, 3, json!({"type":"start"})).await["payload"]["code"],
        "notOwner"
    );
    request(&mut a, 5, json!({"type":"ready","payload":{"ready":true}})).await;
    request(&mut b, 4, json!({"type":"ready","payload":{"ready":true}})).await;
    let loading = request(&mut a, 6, json!({"type":"start"})).await;
    let match_id = loading["payload"]["room"]["currentMatch"]["matchId"]
        .as_str()
        .unwrap();
    assert_eq!(loading["payload"]["room"]["phase"], "loading");
    assert_eq!(
        request(&mut b, 5, json!({"type":"ready","payload":{"ready":false}})).await["payload"]["code"],
        "wrongPhase"
    );
    request(
        &mut a,
        7,
        json!({"type":"loaded","payload":{"matchId":match_id}}),
    )
    .await;
    let countdown = request(
        &mut b,
        6,
        json!({"type":"loaded","payload":{"matchId":match_id}}),
    )
    .await;
    assert_eq!(countdown["payload"]["room"]["phase"], "countdown");
    room_phase(&mut a, "countdown").await;
    b.close(None).await.unwrap();
    let waiting = room_phase(&mut a, "waiting").await;
    assert_eq!(waiting["lastAbortReason"], "disconnected");
    assert_eq!(waiting["members"].as_array().unwrap().len(), 1);
    assert!(waiting["currentMatch"].is_null());
    let mut replacement = server.connect().await;
    assert_eq!(
        request(
            &mut replacement,
            1,
            json!({"type":"hello","payload":{"accountId":"player-b"}})
        )
        .await["type"],
        "welcome"
    );
    let rejoined = request(
        &mut replacement,
        2,
        json!({"type":"joinRoom","payload":{"roomId":id}}),
    )
    .await;
    assert!(
        rejoined["payload"]["room"]["members"][1]["actorId"]
            .as_u64()
            .unwrap()
            > 2
    );
    server.stop().await;
}

#[tokio::test]
async fn admission_requires_hello_and_enforces_version_identity_and_replay_limits() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    assert_eq!(
        request(&mut a, 1, json!({"type":"listRooms"})).await["payload"]["code"],
        "helloRequired"
    );
    a.send(Message::Text(json!({"protocolVersion":2,"requestId":"2","command":{"type":"hello","payload":{"accountId":"player-a"}}}).to_string().into())).await.unwrap();
    assert_eq!(
        receive(&mut a).await["event"]["payload"]["code"],
        "unsupportedVersion"
    );
    request(
        &mut a,
        3,
        json!({"type":"hello","payload":{"accountId":"player-a"}}),
    )
    .await;
    let mut b = server.connect().await;
    assert_eq!(
        request(
            &mut b,
            1,
            json!({"type":"hello","payload":{"accountId":"player-a"}})
        )
        .await["payload"]["code"],
        "accountConnected"
    );
    for id in 4..38 {
        assert_eq!(
            request(&mut a, id, json!({"type":"ping"})).await["type"],
            "pong"
        );
    }
    assert_eq!(
        request(
            &mut a,
            3,
            json!({"type":"hello","payload":{"accountId":"player-a"}})
        )
        .await["payload"]["code"],
        "staleRequest"
    );
    a.send(Message::Text("{invalid json".into())).await.unwrap();
    assert_eq!(
        receive(&mut a).await["event"]["payload"]["code"],
        "badRequest"
    );
    server.stop().await;
}

#[tokio::test]
async fn default_mode_has_health_but_rejects_development_admission() {
    let server = TestServer::start(false).await;
    let mut http = TcpStream::connect(server.address).await.unwrap();
    http.write_all(b"GET /ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .await
        .unwrap();
    let mut response = String::new();
    tokio::time::timeout(Duration::from_secs(5), http.read_to_string(&mut response))
        .await
        .unwrap()
        .unwrap();
    assert!(response.starts_with("HTTP/1.1 200"));
    assert!(response.contains("\"productionAdmission\":false"));
    assert!(response.contains("healthOnly"));
    let result = connect_async(format!("ws://{}/ws", server.address)).await;
    assert!(matches!(result, Err(Error::Http(response)) if response.status().as_u16() == 503));
    server.stop().await;
}

#[tokio::test]
async fn guest_admission_rejects_missing_replayed_and_wrong_identity_tickets() {
    use t7_game_server::auth::Admission;
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    let admission = Admission::default();
    let server = TestServer::start_service(GameServer::with_admission(
        false,
        MapCatalog::default(),
        Some(std::sync::Arc::new(admission)),
    ))
    .await;
    let missing = connect_async(format!("ws://{}/ws", server.address))
        .await
        .unwrap_err();
    assert!(matches!(missing, Error::Http(response) if response.status() == 401));
    async fn login(server: &TestServer, name: &str) -> (String, String) {
        let body = json!({"displayName":name}).to_string();
        let mut stream = TcpStream::connect(server.address).await.unwrap();
        stream.write_all(format!("POST /auth/guest HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", server.address, body.len(), body).as_bytes()).await.unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();
        let (headers, body) = response.split_once("\r\n\r\n").unwrap();
        (headers.to_owned(), body.to_owned())
    }
    assert!(login(&server, "").await.0.contains("400"));
    let (headers, body) = login(&server, "Player").await;
    assert!(headers.starts_with("HTTP/1.1 200"));
    assert!(headers.to_lowercase().contains("cache-control: no-store"));
    let ticket: Value = serde_json::from_str(&body).unwrap();
    let mut upgrade = format!("ws://{}/ws", server.address)
        .into_client_request()
        .unwrap();
    upgrade.headers_mut().insert(
        "Authorization",
        format!("Bearer {}", ticket["ticket"].as_str().unwrap())
            .parse()
            .unwrap(),
    );
    let (mut client, _) = connect_async(upgrade.clone()).await.unwrap();
    assert_eq!(
        request(
            &mut client,
            1,
            json!({"type":"hello", "payload":{"accountId":"someone-else"}})
        )
        .await["payload"]["code"],
        "invalidAccountId"
    );
    assert_eq!(request(&mut client, 2, json!({"type":"gatewayHello", "payload":{"accountId":ticket["accountId"], "displayName":"Player"}})).await["type"], "welcome");
    assert_eq!(
        request(
            &mut client,
            3,
            json!({"type":"createRoom", "payload":{"name":"room", "mapId":1003}})
        )
        .await["type"],
        "room"
    );
    assert!(
        matches!(connect_async(upgrade).await.unwrap_err(), Error::Http(response) if response.status() == 401)
    );
    client.close(None).await.unwrap();
    server.stop().await;
}

#[tokio::test]
async fn pve_spawn_precedes_motion_and_views_keep_monster_identity_and_room_mode() {
    use t7_game_server::{limits::RoomRules, pve::Catalog};
    let catalog=Catalog::from_json(br#"{"scenarios":[{"scenarioId":1,"mapId":2001,"instanceId":2001,"mapResourceId":10088,"playerSpawns":[[0,0,0]],"monsterSpawns":[[3,0,0]],"monster":{"resourceId":11001,"heroId":11001,"weaponId":11000002,"health":100,"speed":1},"waves":[1],"intermissionMs":500}]}"#).unwrap();
    let service = GameServer::with_pve_configuration(
        true,
        MapCatalog::default(),
        None,
        RoomRules::default(),
        catalog,
    );
    let server = TestServer::start_service(service).await;
    let mut client = server.connect().await;
    request(
        &mut client,
        1,
        json!({"type":"hello","payload":{"accountId":"pve-owner"}}),
    )
    .await;
    let create = json!({"type":"createPveRoom","payload":{"name":"waves","scenarioId":1}});
    let room = request(&mut client, 2, create.clone()).await;
    assert_eq!(room["payload"]["room"]["mode"], "pve");
    assert_eq!(request(&mut client, 2, create).await, room);
    let loading = request(&mut client, 3, json!({"type":"start"})).await;
    let id = loading["payload"]["room"]["currentMatch"]["matchId"].clone();
    request(
        &mut client,
        4,
        json!({"type":"loaded","payload":{"matchId":id}}),
    )
    .await;
    room_phase(&mut client, "running").await;
    let spawn = receive(&mut client).await;
    assert_eq!(spawn["event"]["type"], "pveChanged");
    let snapshot = &spawn["event"]["payload"]["update"]["snapshot"];
    assert_eq!(snapshot["phase"], "active");
    let monster = snapshot["monsters"][0]["actorId"].clone();
    let movement = receive(&mut client).await;
    assert_eq!(movement["event"]["type"], "poseChanged");
    assert_eq!(movement["event"]["payload"]["update"]["actorId"], monster);
    let view = request(
        &mut client,
        5,
        json!({"type":"getClientView","payload":{"matchId":id}}),
    )
    .await;
    assert_eq!(view["payload"]["view"]["mode"], "pve");
    assert_eq!(
        view["payload"]["view"]["pve"]["monsters"][0]["actorId"],
        monster
    );
    request(&mut client, 6, json!({"type":"leaveRoom"})).await;
    let rooms = request(&mut client, 7, json!({"type":"listRooms"})).await;
    assert!(rooms["payload"]["rooms"].as_array().unwrap().is_empty());
    client.close(None).await.unwrap();
    server.stop().await;
}

async fn keyed_socket(server: &TestServer, path: &str, key: &str) -> Result<Client, Error> {
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    let mut request = format!("ws://{}{path}", server.address)
        .into_client_request()
        .unwrap();
    request
        .headers_mut()
        .insert("x-t7-motion-key", key.parse().unwrap());
    connect_async(request).await.map(|(socket, _)| socket)
}

#[tokio::test]
async fn motion_channel_requires_a_live_unique_capability_and_closes_with_its_owner() {
    let server = TestServer::start(true).await;
    let key = "19".repeat(32);
    assert!(keyed_socket(&server, "/ws/motion", &key).await.is_err());
    let mut reliable = keyed_socket(&server, "/ws", &key).await.unwrap();
    request(
        &mut reliable,
        1,
        json!({"type":"hello","payload":{"accountId":"lane-owner"}}),
    )
    .await;
    let mut motion = keyed_socket(&server, "/ws/motion", &key).await.unwrap();
    assert!(keyed_socket(&server, "/ws/motion", &key).await.is_err());
    reliable.close(None).await.unwrap();
    let closed = tokio::time::timeout(Duration::from_secs(2), motion.next())
        .await
        .unwrap();
    assert!(!matches!(closed, Some(Ok(Message::Text(_)))));
    assert!(keyed_socket(&server, "/ws/motion", &key).await.is_err());
    server.stop().await;
}

#[tokio::test]
async fn pose_socket_never_overtakes_reliable_sequence_or_carries_action_events() {
    let server = TestServer::start(true).await;
    let key = "ab".repeat(32);
    let mut reliable = keyed_socket(&server, "/ws", &key).await.unwrap();
    request(
        &mut reliable,
        1,
        json!({"type":"hello","payload":{"accountId":"split-owner"}}),
    )
    .await;
    let mut motion = keyed_socket(&server, "/ws/motion", &key).await.unwrap();
    request(
        &mut reliable,
        2,
        json!({"type":"createRoom","payload":{"name":"solo","mapId":1003}}),
    )
    .await;
    let started = request(&mut reliable, 3, json!({"type":"start"})).await;
    let id = started["payload"]["room"]["currentMatch"]["matchId"].clone();
    request(
        &mut reliable,
        4,
        json!({"type":"loaded","payload":{"matchId":id}}),
    )
    .await;
    room_phase(&mut reliable, "running").await;
    let pose_request = serde_json::from_value(json!({"protocolVersion":1,"requestId":"900",
        "command":{"type":"poseInput","payload":{"matchId":id,"inputSeq":1,
            "pose":{"position":[0,0,0.5],"heading":0,"forwardBack":0,"leftRight":0,"running":false}}}})).unwrap();
    motion
        .send(Message::Binary(
            t7_game_server::motion_wire::encode_input(&pose_request)
                .unwrap()
                .into(),
        ))
        .await
        .unwrap();
    let pose = tokio::time::timeout(Duration::from_secs(2), receive_pose(&mut motion))
        .await
        .unwrap();
    assert_eq!(pose["event"]["type"], "poseChanged");
    // Lower request IDs on another stream are not stale.
    assert_eq!(
        request(
            &mut reliable,
            5,
            json!({"type":"actionInput","payload":{
        "matchId":id,"inputSeq":1,"intent":"attackUp"}})
        )
        .await["type"],
        "actionAccepted"
    );
    assert_eq!(
        receive(&mut reliable).await["event"]["type"],
        "actionChanged"
    );
    assert!(
        tokio::time::timeout(Duration::from_millis(80), motion.next())
            .await
            .is_err()
    );
    motion.close(None).await.unwrap();
    // Loss of the replaceable pose lane must not destroy the reliable session.
    assert_eq!(
        request(&mut reliable, 9, json!({"type":"ping"})).await["type"],
        "pong"
    );
    server.stop().await;
}

#[tokio::test]
async fn clock_probe_nonce_is_connection_scoped_and_only_consumed_once() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    request(
        &mut a,
        1,
        json!({"type":"hello","payload":{"accountId":"clock-a"}}),
    )
    .await;
    request(
        &mut b,
        1,
        json!({"type":"hello","payload":{"accountId":"clock-b"}}),
    )
    .await;
    let probe = request(&mut a, 2, json!({"type":"clockProbe"})).await;
    assert_eq!(probe["type"], "clockSample");
    let echo = json!({"type":"clockEcho","payload":{
        "epoch": probe["payload"]["epoch"], "nonce": probe["payload"]["nonce"],
        "receivedMs": 10_000, "sentMs":10_000
    }});
    let wrong_peer = request(&mut b, 2, echo.clone()).await;
    assert_eq!(wrong_peer["payload"]["code"], "badRequest");
    let accepted = request(&mut a, 3, echo.clone()).await;
    assert_eq!(accepted["type"], "clockReady");
    assert_eq!(accepted["payload"]["epoch"], probe["payload"]["epoch"]);
    assert_eq!(accepted["payload"]["samples"], 1);
    assert_eq!(request(&mut a, 3, echo.clone()).await, accepted);
    let replay = request(&mut a, 4, echo).await;
    assert_eq!(replay["payload"]["code"], "badRequest");
    a.close(None).await.unwrap();
    b.close(None).await.unwrap();
    server.stop().await;
}

async fn receive_pose(client: &mut Client) -> Value {
    let Message::Binary(bytes) = client.next().await.unwrap().unwrap() else {
        panic!("motion must be binary");
    };
    let update = t7_game_server::motion_wire::decode_output(&bytes).unwrap();
    json!({"protocolVersion": 1, "requestId": null, "event": {"type": "poseChanged", "payload": {"update": update}}})
}

#[tokio::test]
async fn pipelined_leave_join_keeps_request_order_and_global_identity() {
    let server = TestServer::start(true).await;
    let mut a = server.connect().await;
    let mut b = server.connect().await;
    let mut guest = server.connect().await;
    for (client, account) in [
        (&mut a, "pipeline-a"),
        (&mut b, "pipeline-b"),
        (&mut guest, "pipeline-guest"),
    ] {
        request(
            client,
            1,
            json!({"type":"hello", "payload":{"accountId":account}}),
        )
        .await;
    }
    let a_room = request(
        &mut a,
        2,
        json!({"type":"createRoom", "payload":{"name":"A", "mapId":1003}}),
    )
    .await;
    let b_room = request(
        &mut b,
        2,
        json!({"type":"createRoom", "payload":{"name":"B", "mapId":1003}}),
    )
    .await;
    assert_ne!(
        a_room["payload"]["room"]["roomNumber"],
        b_room["payload"]["room"]["roomNumber"]
    );
    let directory = request(&mut a, 3, json!({"type":"listRooms"})).await;
    assert_eq!(directory["payload"]["rooms"].as_array().unwrap().len(), 2);
    let joined = request(
        &mut guest,
        2,
        json!({"type":"joinRoom", "payload":{"roomId":a_room["payload"]["room"]["roomId"]}}),
    )
    .await;
    let user_id = joined["payload"]["room"]["members"][1]["userId"].clone();
    for (seq, command) in [
        (3, json!({"type":"leaveRoom"})),
        (
            4,
            json!({"type":"joinRoom", "payload":{"roomId":b_room["payload"]["room"]["roomId"]}}),
        ),
        (5, json!({"type":"ping"})),
    ] {
        guest
            .send(Message::Text(
                json!({"protocolVersion":1, "requestId":seq.to_string(), "command":command})
                    .to_string()
                    .into(),
            ))
            .await
            .unwrap();
    }
    let mut replies = Vec::new();
    while replies.len() < 3 {
        let event = receive(&mut guest).await;
        if event["requestId"].is_string() {
            replies.push(event);
        }
    }
    assert_eq!(
        replies
            .iter()
            .map(|e| e["requestId"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["3", "4", "5"]
    );
    assert_eq!(replies[0]["event"]["type"], "left");
    assert_eq!(
        replies[1]["event"]["payload"]["room"]["members"][1]["userId"],
        user_id
    );
    assert_eq!(replies[2]["event"]["type"], "pong");
    let mut duplicate = server.connect().await;
    assert_eq!(
        request(
            &mut duplicate,
            1,
            json!({"type":"hello", "payload":{"accountId":"pipeline-guest"}})
        )
        .await["payload"]["code"],
        "accountConnected"
    );
    for client in [&mut a, &mut b, &mut guest, &mut duplicate] {
        client.close(None).await.unwrap();
    }
    server.stop().await;
}

#[tokio::test]
async fn last_member_can_recreate_rooms_without_reidentification_or_number_reuse() {
    let server = TestServer::start(true).await;
    let mut client = server.connect().await;
    let welcome = request(
        &mut client,
        1,
        json!({"type":"hello", "payload":{"accountId":"repeat-owner"}}),
    )
    .await;
    let mut last_number = 0;
    for round in 0..16 {
        let room = request(
            &mut client,
            2 + round * 2,
            json!({"type":"createRoom", "payload":{"name":"again", "mapId":1003}}),
        )
        .await;
        assert_eq!(room["type"], "room");
        let number = room["payload"]["room"]["roomNumber"].as_u64().unwrap();
        assert!(number > last_number);
        last_number = number;
        assert_eq!(
            room["payload"]["room"]["members"][0]["userId"],
            welcome["payload"]["userId"]
        );
        assert_eq!(
            request(&mut client, 3 + round * 2, json!({"type":"leaveRoom"})).await["type"],
            "left"
        );
    }
    client.close(None).await.unwrap();
    server.stop().await;
}

#[tokio::test]
async fn motion_lane_rejects_json_and_binary_input_never_enters_control_lane() {
    for motion in [true, false] {
        let server = TestServer::start(true).await;
        let key = "cd".repeat(32);
        let mut reliable = keyed_socket(&server, "/ws", &key).await.unwrap();
        request(
            &mut reliable,
            1,
            json!({"type":"hello", "payload":{"accountId":"format-owner"}}),
        )
        .await;
        let mut lane = keyed_socket(&server, "/ws/motion", &key).await.unwrap();
        if motion {
            lane.send(Message::Text("{}".into())).await.unwrap();
        } else {
            reliable
                .send(Message::Binary(vec![0; 119].into()))
                .await
                .unwrap();
        }
        let closed = tokio::time::timeout(Duration::from_secs(2), lane.next())
            .await
            .unwrap();
        assert!(!matches!(
            closed,
            Some(Ok(Message::Binary(_))) | Some(Ok(Message::Text(_)))
        ));
        let _ = reliable.close(None).await;
        let _ = lane.close(None).await;
        server.stop().await;
    }
}

#[tokio::test]
async fn reconnect_keeps_room_owner_roster_and_identity_without_reusing_login() {
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    let server = TestServer::start(true).await;
    let key = "ab".repeat(32);
    let mut handshake = format!("ws://{}/ws", server.address)
        .into_client_request()
        .unwrap();
    handshake
        .headers_mut()
        .insert("x-t7-motion-key", key.parse().unwrap());
    let mut owner = connect_async(handshake).await.unwrap().0;
    let welcome = request(
        &mut owner,
        1,
        json!({"type":"hello","payload":{"accountId":"resume-owner"}}),
    )
    .await;
    let created = request(
        &mut owner,
        2,
        json!({"type":"createRoom","payload":{"name":"resume","mapId":1003}}),
    )
    .await;
    let original = created["payload"]["room"].clone();
    let mut peer = server.connect().await;
    request(
        &mut peer,
        1,
        json!({"type":"hello","payload":{"accountId":"resume-peer"}}),
    )
    .await;
    let joined = request(
        &mut peer,
        2,
        json!({"type":"joinRoom","payload":{"roomId":original["roomId"]}}),
    )
    .await;
    let roster = joined["payload"]["room"]["members"].clone();
    request(
        &mut peer,
        3,
        json!({"type":"ready","payload":{"ready":true}}),
    )
    .await;
    let loading = request(&mut owner, 3, json!({"type":"start"})).await;
    let match_id = loading["payload"]["room"]["currentMatch"]["matchId"].clone();
    request(
        &mut owner,
        4,
        json!({"type":"loaded","payload":{"matchId":match_id}}),
    )
    .await;
    request(
        &mut peer,
        4,
        json!({"type":"loaded","payload":{"matchId":match_id}}),
    )
    .await;
    room_phase(&mut owner, "running").await;
    room_phase(&mut peer, "running").await;
    request(&mut owner, 5, json!({"type":"actionInput","payload":{"matchId":match_id,"inputSeq":1,"intent":"blockRight"}})).await;
    drop(owner); // Abrupt transport loss, no LeaveRoom.
    tokio::time::sleep(Duration::from_millis(100)).await;
    let mut handshake = format!("ws://{}/ws/resume", server.address)
        .into_client_request()
        .unwrap();
    handshake
        .headers_mut()
        .insert("x-t7-motion-key", key.parse().unwrap());
    let mut resumed = connect_async(handshake).await.unwrap().0;
    let event = receive(&mut resumed).await;
    assert_eq!(event["event"]["type"], "sessionResumed");
    let payload = &event["event"]["payload"];
    assert_eq!(payload["userId"], welcome["payload"]["userId"]);
    assert_eq!(payload["room"]["roomId"], original["roomId"]);
    assert_eq!(payload["room"]["ownerAccountId"], "resume-owner");
    assert_eq!(payload["room"]["phase"], "running");
    assert_eq!(payload["room"]["currentMatch"]["matchId"], match_id);
    assert_eq!(
        payload["room"]["members"].as_array().unwrap().len(),
        roster.as_array().unwrap().len()
    );
    assert_eq!(payload["recovery"]["lives"][0]["health"], 100);
    assert_eq!(payload["recovery"]["actions"][0]["state"], 2);
    assert_eq!(
        request(&mut resumed, 6, json!({"type":"ping"})).await["type"],
        "pong"
    );
    assert_eq!(
        request(&mut resumed, 7, json!({"type":"leaveRoom"})).await["type"],
        "left"
    );
    let leaving = receive(&mut peer).await;
    // Drain any earlier join notification before the owner's explicit departure.
    let mut leaving = leaving;
    while leaving["event"]["type"] != "roomChanged"
        || leaving["event"]["payload"]["room"]["members"]
            .as_array()
            .is_none_or(|m| m.len() != 1)
    {
        leaving = receive(&mut peer).await;
    }
    assert_eq!(
        leaving["event"]["payload"]["room"]["ownerAccountId"],
        "resume-peer"
    );
    server.stop().await;
}

#[tokio::test]
async fn reconnect_rejects_unknown_capabilities() {
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    let server = TestServer::start(true).await;
    let mut handshake = format!("ws://{}/ws/resume", server.address)
        .into_client_request()
        .unwrap();
    handshake
        .headers_mut()
        .insert("x-t7-motion-key", "cd".repeat(32).parse().unwrap());
    let error = connect_async(handshake).await.unwrap_err();
    assert!(matches!(error, Error::Http(response) if response.status() == 401));
    server.stop().await;
}
