//! Fixed little-endian motion frames. Control and combat messages remain JSON.
use crate::{
    input_timeline::{Source, Stamp},
    motion::{MotionFrame, Pose, PoseEvent},
    protocol::{ClientCommand, ClientRequest, VERSION},
};

pub const INPUT_BYTES: usize = 119;
pub const OUTPUT_BYTES: usize = 107;

struct Reader<'a>(&'a [u8]);
impl Reader<'_> {
    fn bytes<const N: usize>(&mut self) -> [u8; N] {
        let (value, rest) = self.0.split_at(N);
        self.0 = rest;
        value.try_into().unwrap()
    }
    fn u64(&mut self) -> u64 {
        u64::from_le_bytes(self.bytes())
    }
    fn u32(&mut self) -> u32 {
        u32::from_le_bytes(self.bytes())
    }
    fn uuid(&mut self) -> String {
        uuid::Uuid::from_bytes(self.bytes()).to_string()
    }
    fn vector(&mut self) -> [f32; 3] {
        std::array::from_fn(|_| f32::from_le_bytes(self.bytes()))
    }
    fn pose(&mut self) -> Option<(Pose, bool)> {
        let position = self.vector();
        let heading = i16::from_le_bytes(self.bytes());
        let forward_back = i8::from_le_bytes(self.bytes());
        let left_right = i8::from_le_bytes(self.bytes());
        let flags = self.bytes::<1>()[0];
        let tick = self.u64();
        let action_tick = self.u64();
        let action_id = u16::from_le_bytes(self.bytes());
        let velocity = self.vector();
        if flags & !31 != 0
            || flags & 2 == 0
                && (flags & 12 != 0
                    || tick != 0
                    || action_tick != 0
                    || action_id != 0
                    || velocity != [0.; 3])
        {
            return None;
        }
        let pose = Pose {
            position,
            heading,
            forward_back,
            left_right,
            running: flags & 1 != 0,
            motion: (flags & 2 != 0).then_some(MotionFrame {
                tick,
                action_tick,
                action_id,
                velocity,
                airborne: flags & 4 != 0,
                jumping: flags & 8 != 0,
            }),
        };
        pose.valid().then_some((pose, flags & 16 != 0))
    }
}

pub fn decode_input(bytes: &[u8]) -> Option<ClientRequest> {
    if bytes.len() != INPUT_BYTES || &bytes[..4] != b"T7M1" {
        return None;
    }
    let mut reader = Reader(&bytes[4..]);
    let sequence = reader.u64();
    let match_id = reader.uuid();
    let input_seq = reader.u32();
    let (pose, stamped) = reader.pose()?;
    let epoch = reader.bytes::<16>();
    let combat_seq = reader.u64();
    let sampled_ms = reader.u64();
    let gateway_ms = reader.u64();
    if sequence == 0
        || input_seq == 0
        || sampled_ms > gateway_ms
        || !stamped && (epoch != [0; 16] || combat_seq != 0 || sampled_ms != 0 || gateway_ms != 0)
    {
        return None;
    }
    Some(ClientRequest {
        protocol_version: VERSION,
        request_id: sequence.to_string(),
        command: ClientCommand::PoseInput {
            match_id,
            input_seq,
            pose,
        },
        timeline: stamped.then(|| Stamp {
            epoch: uuid::Uuid::from_bytes(epoch).to_string(),
            combat_seq,
            sampled_ms,
            gateway_ms,
            source: Source::NativePose,
        }),
    })
}

fn write_pose(bytes: &mut Vec<u8>, pose: &Pose) {
    for v in pose.position {
        bytes.extend(v.to_le_bytes());
    }
    bytes.extend(pose.heading.to_le_bytes());
    bytes.extend(pose.forward_back.to_le_bytes());
    bytes.extend(pose.left_right.to_le_bytes());
    let frame = pose.motion.as_ref();
    bytes.push(
        u8::from(pose.running)
            | if let Some(f) = frame {
                2 | (u8::from(f.airborne) << 2) | (u8::from(f.jumping) << 3)
            } else {
                0
            },
    );
    bytes.extend(frame.map_or(0, |f| f.tick).to_le_bytes());
    bytes.extend(frame.map_or(0, |f| f.action_tick).to_le_bytes());
    bytes.extend(frame.map_or(0, |f| f.action_id).to_le_bytes());
    for v in frame.map_or([0.; 3], |f| f.velocity) {
        bytes.extend(v.to_le_bytes());
    }
}

pub fn encode_input(request: &ClientRequest) -> Option<Vec<u8>> {
    let ClientCommand::PoseInput {
        match_id,
        input_seq,
        pose,
    } = &request.command
    else {
        return None;
    };
    if request.protocol_version != VERSION || *input_seq == 0 || !pose.valid() {
        return None;
    }
    let mut bytes = Vec::with_capacity(INPUT_BYTES);
    bytes.extend(b"T7M1");
    bytes.extend(request.sequence().ok()?.to_le_bytes());
    bytes.extend(uuid::Uuid::parse_str(match_id).ok()?.as_bytes());
    bytes.extend(input_seq.to_le_bytes());
    write_pose(&mut bytes, pose);
    if let Some(stamp) = &request.timeline {
        if stamp.source != Source::NativePose || stamp.sampled_ms > stamp.gateway_ms {
            return None;
        }
        bytes[48] |= 16;
        bytes.extend(uuid::Uuid::parse_str(&stamp.epoch).ok()?.as_bytes());
        bytes.extend(stamp.combat_seq.to_le_bytes());
        bytes.extend(stamp.sampled_ms.to_le_bytes());
        bytes.extend(stamp.gateway_ms.to_le_bytes());
    } else {
        bytes.extend([0; 40]);
    }
    Some(bytes)
}

pub fn decode_output(bytes: &[u8]) -> Option<PoseEvent> {
    if bytes.len() != OUTPUT_BYTES || &bytes[..4] != b"T7S1" {
        return None;
    }
    let mut reader = Reader(&bytes[4..]);
    let room_id = reader.uuid();
    let match_id = reader.uuid();
    let actor_id = crate::client_view::ActorId(reader.u32());
    let event_seq = reader.u64();
    let input_seq = reader.u32();
    let server_time_ms = reader.u64();
    let (pose, stamped) = reader.pose()?;
    if actor_id.0 == 0 || event_seq == 0 || input_seq == 0 || stamped {
        return None;
    }
    Some(PoseEvent {
        room_id,
        match_id,
        actor_id,
        event_seq,
        input_seq,
        server_time_ms,
        pose,
    })
}

pub fn encode_output(event: &PoseEvent) -> Option<Vec<u8>> {
    let mut bytes = Vec::with_capacity(OUTPUT_BYTES);
    bytes.extend(b"T7S1");
    bytes.extend(uuid::Uuid::parse_str(&event.room_id).ok()?.as_bytes());
    bytes.extend(uuid::Uuid::parse_str(&event.match_id).ok()?.as_bytes());
    bytes.extend(event.actor_id.0.to_le_bytes());
    bytes.extend(event.event_seq.to_le_bytes());
    bytes.extend(event.input_seq.to_le_bytes());
    bytes.extend(event.server_time_ms.to_le_bytes());
    write_pose(&mut bytes, &event.pose);
    Some(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input() -> Vec<u8> {
        let mut bytes = b"T7M1".to_vec();
        bytes.extend(900u64.to_le_bytes());
        bytes.extend(uuid::Uuid::nil().as_bytes());
        bytes.extend(1u32.to_le_bytes());
        write_pose(
            &mut bytes,
            &Pose {
                position: [1., 2., 0.5],
                heading: 45,
                forward_back: -1,
                left_right: 0,
                running: false,
                motion: None,
            },
        );
        bytes.extend([0; 40]);
        assert_eq!(bytes.len(), INPUT_BYTES);
        bytes
    }
    #[test]
    fn fixed_frames_reject_bad_lengths_flags_nonfinite_values_and_sequences() {
        let bytes = input();
        let request = decode_input(&bytes).unwrap();
        assert_eq!(request.sequence(), Ok(900));
        for n in 0..INPUT_BYTES {
            assert!(decode_input(&bytes[..n]).is_none());
        }
        let mut bad = bytes.clone();
        bad.push(0);
        assert!(decode_input(&bad).is_none());
        let mut bad = bytes.clone();
        bad[48] = 128;
        assert!(decode_input(&bad).is_none());
        let mut bad = bytes.clone();
        bad[32..36].copy_from_slice(&f32::NAN.to_le_bytes());
        assert!(decode_input(&bad).is_none());
        let mut bad = bytes;
        bad[4..12].fill(0);
        assert!(decode_input(&bad).is_none());
    }
    #[test]
    fn output_layout_preserves_full_precision_timestamps_and_airborne_state() {
        let event = PoseEvent {
            room_id: uuid::Uuid::nil().to_string(),
            match_id: uuid::Uuid::nil().to_string(),
            actor_id: crate::client_view::ActorId(2),
            event_seq: u64::MAX,
            input_seq: 3,
            server_time_ms: 100,
            pose: Pose {
                position: [1., 2., 3.],
                heading: -90,
                forward_back: -1,
                left_right: 1,
                running: true,
                motion: Some(MotionFrame {
                    tick: 101,
                    action_tick: 99,
                    action_id: 54,
                    velocity: [1., 2., -3.],
                    airborne: true,
                    jumping: true,
                }),
            },
        };
        let bytes = encode_output(&event).unwrap();
        assert_eq!(bytes.len(), OUTPUT_BYTES);
        assert_eq!(&bytes[..4], b"T7S1");
        let mut reader = Reader(&bytes[4..]);
        assert_eq!(reader.uuid(), event.room_id);
        assert_eq!(reader.uuid(), event.match_id);
        assert_eq!(reader.u32(), 2);
        assert_eq!(reader.u64(), u64::MAX);
        assert_eq!(reader.u32(), 3);
        assert_eq!(reader.u64(), 100);
        assert_eq!(reader.pose(), Some((event.pose, false)));
    }
}
