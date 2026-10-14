using System;
using System.IO;
using System.Runtime.ExceptionServices;
using System.Threading;
using System.Threading.Tasks;
using T7.Rekindle.Core;

namespace T7.Rekindle.Battle
{
    // Construct only after admission. The bound identity comes from the room
    // assignment, never from the first untrusted frame or the game's AUTH.
    public sealed class BattleGameRelay : IDisposable
    {
        public const int MaxDataSize = 64 * 1024;
        private readonly BattleConnection _connection;
        private readonly ulong _roomId, _matchId, _playerId;
        private readonly Func<BattleChannel, CancellationToken, Task<Stream>> _openChannel;
        private readonly Channel[] _channels = new Channel[4];
        private readonly object _gate = new object();
        private readonly SemaphoreSlim _sender = new SemaphoreSlim(1, 1);
        private readonly CancellationTokenSource _stop = new CancellationTokenSource();
        private Exception _error;
        private ulong _receivedSequence, _sentSequence;
        private int _started, _disposed;

        public BattleGameRelay(BattleConnection connection, ulong roomId, ulong matchId, ulong playerId,
            Func<BattleChannel, CancellationToken, Task<Stream>> openChannel)
        {
            if (roomId == 0) throw new ArgumentOutOfRangeException(nameof(roomId));
            if (matchId == 0) throw new ArgumentOutOfRangeException(nameof(matchId));
            if (playerId == 0) throw new ArgumentOutOfRangeException(nameof(playerId));
            _connection = connection ?? throw new ArgumentNullException(nameof(connection));
            _openChannel = openChannel ?? throw new ArgumentNullException(nameof(openChannel));
            _roomId = roomId; _matchId = matchId; _playerId = playerId;
        }

        public async Task RunAsync(CancellationToken cancellationToken)
        {
            if (Interlocked.Exchange(ref _started, 1) != 0) throw new InvalidOperationException("A relay runs once.");
            if (Volatile.Read(ref _disposed) != 0) throw new ObjectDisposedException(nameof(BattleGameRelay));
            using (cancellationToken.Register(Dispose))
            {
                try
                {
                    while (true)
                    {
                        var frame = await _connection.ReadAsync(_stop.Token).ConfigureAwait(false);
                        if (frame == null) break;
                        await ReceiveAsync(frame).ConfigureAwait(false);
                    }
                }
                catch (Exception error)
                {
                    if (Volatile.Read(ref _disposed) == 0) RecordError(error);
                }
                finally
                {
                    Dispose();
                    // All three readers are joined before the host may be stopped.
                    foreach (var channel in _channels)
                        if (channel?.Reader != null) await channel.Reader.ConfigureAwait(false);
                }
            }
            cancellationToken.ThrowIfCancellationRequested();
            if (_error != null) ExceptionDispatchInfo.Capture(_error).Throw();
        }

        private async Task ReceiveAsync(BattleFrame frame)
        {
            if (frame.RoomId != _roomId || frame.MatchId != _matchId || frame.PlayerId != _playerId)
                throw new InvalidDataException("Game frame targets another assigned identity.");
            if (frame.Sequence <= _receivedSequence) throw new InvalidDataException("Repeated or out-of-order game frame.");
            _receivedSequence = frame.Sequence;
            if (frame.Kind != BattleMessageKind.OpenChannel && frame.Kind != BattleMessageKind.GameData
                && frame.Kind != BattleMessageKind.CloseChannel)
                throw new InvalidDataException("Expected a game channel frame.");
            if (frame.Kind == BattleMessageKind.GameData ? frame.PayloadLength == 0 || frame.PayloadLength > MaxDataSize
                : frame.PayloadLength != 0)
                throw new InvalidDataException("Invalid game channel payload length.");

            Channel channel;
            lock (_gate) channel = _channels[(int)frame.Channel];
            if (frame.Kind == BattleMessageKind.OpenChannel)
            {
                if (channel != null) throw new InvalidDataException("Game channel was already opened.");
                var stream = await _openChannel(frame.Channel, _stop.Token).ConfigureAwait(false);
                if (stream == null) throw new IOException("Game channel factory returned no stream.");
                channel = new Channel(frame.Channel, stream);
                if (!stream.CanRead || !stream.CanWrite) { channel.Close(); throw new IOException("Game channel is not duplex."); }
                lock (_gate)
                {
                    if (Volatile.Read(ref _disposed) != 0) { channel.Close(); return; }
                    _channels[(int)frame.Channel] = channel;
                }
                channel.Reader = PumpAsync(channel);
                return;
            }
            if (channel == null) throw new InvalidDataException("Game channel has not been opened.");
            if (frame.Kind == BattleMessageKind.CloseChannel) { channel.Close(); return; }
            // A full close can race with bytes already in flight in the other direction.
            if (channel.Closed) return;
            var bytes = frame.GetPayload();
            try { await channel.Stream.WriteAsync(bytes, 0, bytes.Length, _stop.Token).ConfigureAwait(false); }
            catch (Exception) when (channel.Closed) { }
        }

        private async Task PumpAsync(Channel channel)
        {
            try
            {
                var buffer = new byte[MaxDataSize];
                while (!channel.Closed)
                {
                    int count;
                    try { count = await channel.Stream.ReadAsync(buffer, 0, buffer.Length, _stop.Token).ConfigureAwait(false); }
                    catch (Exception) when (channel.Closed || Volatile.Read(ref _disposed) != 0) { return; }
                    if (count == 0)
                    {
                        if (channel.Close()) await SendAsync(BattleMessageKind.CloseChannel, channel.Role, new byte[0]).ConfigureAwait(false);
                        return;
                    }
                    var payload = new byte[count];
                    Buffer.BlockCopy(buffer, 0, payload, 0, count);
                    await SendAsync(BattleMessageKind.GameData, channel.Role, payload).ConfigureAwait(false);
                }
            }
            catch (Exception error)
            {
                if (Volatile.Read(ref _disposed) == 0) { RecordError(error); Dispose(); }
            }
        }

        private async Task SendAsync(BattleMessageKind kind, BattleChannel role, byte[] bytes)
        {
            // At most three readers enqueue one chunk each. Assigning the sequence
            // inside this gate keeps it in exactly the same order as the wire writes.
            await _sender.WaitAsync(_stop.Token).ConfigureAwait(false);
            try
            {
                if (_sentSequence == ulong.MaxValue) throw new IOException("Game frame sequence exhausted.");
                await _connection.WriteAsync(new BattleFrame(kind, role, _roomId, _matchId, _playerId,
                    ++_sentSequence, bytes), _stop.Token).ConfigureAwait(false);
            }
            finally { _sender.Release(); }
        }

        public void Dispose()
        {
            if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
            try { _stop.Cancel(); } catch (Exception error) { RecordError(error); }
            try { _connection.Dispose(); } catch (Exception error) { RecordError(error); }
            lock (_gate)
                foreach (var channel in _channels)
                    try { channel?.Close(); } catch (Exception error) { RecordError(error); }
        }

        private void RecordError(Exception error) { Interlocked.CompareExchange(ref _error, error, null); }

        private sealed class Channel
        {
            internal readonly BattleChannel Role;
            internal readonly Stream Stream;
            internal Task Reader;
            private int _closed;
            internal bool Closed => Volatile.Read(ref _closed) != 0;
            internal Channel(BattleChannel role, Stream stream) { Role = role; Stream = stream; }
            internal bool Close()
            {
                if (Interlocked.Exchange(ref _closed, 1) != 0) return false;
                Stream.Dispose();
                return true;
            }
        }
    }
}
