using System;
using System.Runtime.InteropServices;
using T7R.Core;

namespace T7R.Desktop.Services
{
    public sealed partial class NativeBridgeService
    {
        public void SetLogLevel(DiagnosticLogLevel level)
        {
            if (!DiagnosticLogLevels.IsValid(level)) throw new ArgumentOutOfRangeException(nameof(level));
            var status = InvokeNative(handle => _native.SetLogLevel(handle, (uint)level));
            if (status != NativeStatus.Ok) throw new InvalidOperationException("设置日志等级失败：" + status);
        }

        private sealed partial class NativeMethods
        {
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus LogLevelDelegate(IntPtr session, uint level);
            internal readonly LogLevelDelegate SetLogLevel;
        }
    }
}
