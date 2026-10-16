using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
using T7R.Core;

namespace T7R.Battle
{
    // Shared by the launcher-side host and the headless console. No WPF or game
    // process lifecycle belongs here. Native release joins its workers first.
    public sealed class NativeBattleHost : IDisposable
    {
        private readonly object _sync = new object();
        private readonly LibraryHandle _library;
        private readonly HostHandle _host;
        private readonly HostCall _start, _stop;
        private readonly SnapshotCall _snapshot;
        private readonly ErrorCall _error;
        private bool _disposed;

        public NativeBattleHost(string packageRoot, string clientDirectory, string writableRoot)
        {
            ValidatePath(packageRoot, nameof(packageRoot));
            ValidatePath(clientDirectory, nameof(clientDirectory));
            ValidatePath(writableRoot, nameof(writableRoot));
            if (IntPtr.Size != 8 || Marshal.SizeOf<NativeBattleHostCreateArgs>() != 80
                || Marshal.SizeOf<NativeBattleHostSnapshot>() != 104)
                throw new PlatformNotSupportedException("Battle host requires the x64 ABI.");
            _library = new LibraryHandle(Path.Combine(packageRoot, NativeBridgeContract.LibraryName));
            try
            {
                var create = Function<CreateCall>("t7r_battle_host_create");
                var release = Function<ReleaseCall>("t7r_battle_host_release");
                _start = Function<HostCall>("t7r_battle_host_start");
                _stop = Function<HostCall>("t7r_battle_host_stop");
                _snapshot = Function<SnapshotCall>("t7r_battle_host_snapshot");
                _error = Function<ErrorCall>("t7r_battle_host_error");
                using (var root = new Utf8Path(packageRoot))
                using (var client = new Utf8Path(clientDirectory))
                using (var writable = new Utf8Path(writableRoot))
                {
                    var args = new NativeBattleHostCreateArgs
                    {
                        AbiVersion = NativeBridgeContract.AbiVersion, StructSize = 80,
                        PackageRoot = root.Value, ClientDirectory = client.Value, WritableRoot = writable.Value
                    };
                    IntPtr handle;
                    var status = create(ref args, out handle);
                    if (status != NativeStatus.Ok) throw new InvalidOperationException("Create battle host: " + status);
                    _host = new HostHandle(handle, _library, release);
                }
            }
            catch { _library.Dispose(); throw; }
        }

        public void Start() { lock (_sync) { EnsureOpen(); Check(_start(_host)); } }
        public void Stop() { lock (_sync) { EnsureOpen(); Check(_stop(_host)); } }

        public BattleHostSnapshot GetSnapshot()
        {
            lock (_sync)
            {
                EnsureOpen();
                var snapshot = new NativeBattleHostSnapshot { AbiVersion = NativeBridgeContract.AbiVersion, StructSize = 104 };
                Check(_snapshot(_host, ref snapshot));
                var length = Array.IndexOf(snapshot.Phase, (byte)0);
                return new BattleHostSnapshot(snapshot, Encoding.UTF8.GetString(snapshot.Phase, 0,
                    length < 0 ? snapshot.Phase.Length : length));
            }
        }

        public string GetError() { lock (_sync) { EnsureOpen(); return ReadError(); } }

        public void Dispose()
        {
            lock (_sync)
            {
                if (_disposed) return;
                _disposed = true;
                _host.Dispose();
                _library.Dispose();
            }
        }

        private void Check(NativeStatus status)
        {
            if (status != NativeStatus.Ok)
                throw new InvalidOperationException("Battle host: " + status + ". " + ReadError());
        }

        private string ReadError()
        {
            uint required;
            var status = _error(_host, null, 0, out required);
            if (status == NativeStatus.Ok && required == 0) return string.Empty;
            if (status != NativeStatus.BufferTooSmall || required > 1024 * 1024)
                throw new InvalidOperationException("Read battle error: " + status);
            var bytes = new byte[required];
            status = _error(_host, bytes, required, out required);
            if (status != NativeStatus.Ok) throw new InvalidOperationException("Read battle error: " + status);
            return Encoding.UTF8.GetString(bytes, 0, checked((int)required));
        }

        private T Function<T>(string name) where T : Delegate
        {
            var address = GetProcAddress(_library, name);
            if (address == IntPtr.Zero) throw new EntryPointNotFoundException(name);
            return Marshal.GetDelegateForFunctionPointer<T>(address);
        }

        private void EnsureOpen()
        {
            if (_disposed) throw new ObjectDisposedException(nameof(NativeBattleHost));
        }

        private static void ValidatePath(string value, string name)
        {
            if (!NativeBridgeContract.IsUtf8PathAcceptable(value))
                throw new ArgumentException("Expected an absolute UTF-8 path.", name);
        }

        private sealed class Utf8Path : IDisposable
        {
            public NativePath Value { get; }
            public Utf8Path(string value)
            {
                var bytes = new UTF8Encoding(false, true).GetBytes(value);
                var pointer = Marshal.AllocHGlobal(bytes.Length);
                Marshal.Copy(bytes, 0, pointer, bytes.Length);
                Value = new NativePath { AbiVersion = 1, StructSize = 24, Data = pointer, Length = (uint)bytes.Length };
            }
            public void Dispose() { Marshal.FreeHGlobal(Value.Data); }
        }

        private sealed class LibraryHandle : SafeHandleZeroOrMinusOneIsInvalid
        {
            public LibraryHandle(string path) : base(true)
            {
                SetHandle(LoadLibraryEx(path, IntPtr.Zero, 0x100 | 0x800));
                if (IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "Loading battle runtime failed.");
            }
            protected override bool ReleaseHandle() { return FreeLibrary(handle); }
        }

        private sealed class HostHandle : SafeHandleZeroOrMinusOneIsInvalid
        {
            private readonly LibraryHandle _library;
            private readonly ReleaseCall _release;
            public HostHandle(IntPtr value, LibraryHandle library, ReleaseCall release) : base(true)
            {
                bool retained = false;
                library.DangerousAddRef(ref retained);
                _library = library;
                _release = release;
                SetHandle(value);
            }
            protected override bool ReleaseHandle()
            {
                var status = _release(handle);
                _library.DangerousRelease();
                return status == NativeStatus.Ok;
            }
        }

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate NativeStatus CreateCall(ref NativeBattleHostCreateArgs args, out IntPtr host);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate NativeStatus HostCall(HostHandle host);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate NativeStatus ReleaseCall(IntPtr host);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate NativeStatus SnapshotCall(HostHandle host, ref NativeBattleHostSnapshot snapshot);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate NativeStatus ErrorCall(HostHandle host, [Out] byte[] buffer, uint capacity, out uint required);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Ansi, ExactSpelling = true, SetLastError = true)]
        private static extern IntPtr GetProcAddress(LibraryHandle library, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool FreeLibrary(IntPtr library);
    }
}
