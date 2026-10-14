using System.Runtime.InteropServices;

namespace T7.Rekindle.Core
{
    public enum BattleHostState : uint { Stopped = 0, Running = 1, Failed = 2 }

    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeBattleHostCreateArgs
    {
        public uint AbiVersion, StructSize;
        public NativePath PackageRoot, ClientDirectory, WritableRoot;
    }

    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeBattleHostSnapshot
    {
        public uint AbiVersion, StructSize;
        public BattleHostState State;
        public uint Reserved;
        public ushort LoginPort, LogicPort, InstancePort, ReservedPort;
        public uint Connections, Timers;
        public ulong StartedAt;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 64)]
        public byte[] Phase;
    }

    public sealed class BattleHostSnapshot
    {
        public BattleHostState State { get; }
        public ushort LoginPort { get; }
        public ushort LogicPort { get; }
        public ushort InstancePort { get; }
        public uint Connections { get; }
        public uint Timers { get; }
        public string Phase { get; }

        public BattleHostSnapshot(NativeBattleHostSnapshot native, string phase)
        {
            State = native.State;
            LoginPort = native.LoginPort;
            LogicPort = native.LogicPort;
            InstancePort = native.InstancePort;
            Connections = native.Connections;
            Timers = native.Timers;
            Phase = phase;
        }
    }
}
