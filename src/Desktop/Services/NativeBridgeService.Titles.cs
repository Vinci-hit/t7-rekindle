using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Linq;
using Newtonsoft.Json.Linq;
using T7R.Core;

namespace T7R.Desktop.Services
{
    [StructLayout(LayoutKind.Sequential, Pack = 8)]
    public struct NativeTitleRequest
    {
        public uint AbiVersion, StructSize;
        public ulong RequestId, Epoch;
        public uint TitleId, Reserved;
    }
    public interface INativeTitles
    {
        void PublishTitles(ulong epoch, string json, ulong requestId = 0, bool success = true);
        NativeTitleRequest ReadTitleRequest();
    }
    public sealed partial class NativeBridgeService : INativeTitles
    {
        public void PublishTitles(ulong epoch, string json, ulong requestId = 0, bool success = true)
        {
            var bytes = Encoding.UTF8.GetBytes(json);
            var descriptions = TitleDescriptions(json);
            var pointer = Marshal.AllocHGlobal(bytes.Length);
            try
            {
                Marshal.Copy(bytes, 0, pointer, bytes.Length);
                var path = new NativePath { AbiVersion = NativeBridgeContract.AbiVersion,
                    StructSize = (uint)Marshal.SizeOf(typeof(NativePath)), Data = pointer, Length = (uint)bytes.Length };
                var status = InvokeNative(handle => _native.PublishTitles(handle, epoch, ref path, requestId, success ? 1u : 0u,
                    descriptions, (uint)descriptions.Length));
                if (status != NativeStatus.Ok) throw new InvalidOperationException("称号同步失败：" + status);
            }
            finally { Marshal.FreeHGlobal(pointer); }
        }
        internal static byte[] TitleDescriptions(string json)
        {
            var items = (JArray)JObject.Parse(json)["titles"];
            if (items.Count == 0) return Array.Empty<byte>();
            var encoding = Encoding.GetEncoding(936, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);
            var result = new byte[256];
            for (int i = 0; i < 2; ++i)
            {
                var item = items.FirstOrDefault(t => (int)t["titleId"] == 1007 + i);
                if (item == null) throw new System.IO.InvalidDataException("称号目录缺少原客户端资源。");
                var value = encoding.GetBytes((string)item["description"] + "\n" + (string)item["rule"]);
                if (value.Length >= 128) throw new System.IO.InvalidDataException("称号说明超过客户端容量。");
                Buffer.BlockCopy(value, 0, result, i * 128, value.Length);
            }
            return result;
        }
        public NativeTitleRequest ReadTitleRequest()
        {
            var request = new NativeTitleRequest { AbiVersion = NativeBridgeContract.AbiVersion,
                StructSize = (uint)Marshal.SizeOf(typeof(NativeTitleRequest)) };
            var status = InvokeNative(handle => _native.GetTitleRequest(handle, ref request));
            if (status != NativeStatus.Ok) throw new InvalidOperationException("读取称号请求失败：" + status);
            return request;
        }
        private sealed partial class NativeMethods
        {
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus PublishTitlesDelegate(IntPtr session, ulong epoch, ref NativePath json, ulong requestId, uint success,
                [In] byte[] descriptions, uint descriptionLength);
            [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
            internal delegate NativeStatus GetTitleRequestDelegate(IntPtr session, ref NativeTitleRequest request);
            internal readonly PublishTitlesDelegate PublishTitles;
            internal readonly GetTitleRequestDelegate GetTitleRequest;
        }
    }
}
