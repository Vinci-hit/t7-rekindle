using System;

namespace T7R.Core
{
    public enum DiagnosticLogLevel : uint { Debug = 0, Info = 1, Warn = 2, Error = 3 }

    public static class DiagnosticLogLevels
    {
        public static bool IsValid(DiagnosticLogLevel level) => level <= DiagnosticLogLevel.Error;

        public static string Name(DiagnosticLogLevel level)
        {
            switch (level)
            {
                case DiagnosticLogLevel.Debug: return "DEBUG";
                case DiagnosticLogLevel.Info: return "INFO";
                case DiagnosticLogLevel.Warn: return "WARN";
                case DiagnosticLogLevel.Error: return "ERROR";
                default: throw new ArgumentOutOfRangeException(nameof(level));
            }
        }

        public static string DisplayName(DiagnosticLogLevel level)
        {
            switch (level)
            {
                case DiagnosticLogLevel.Debug: return "调试";
                case DiagnosticLogLevel.Info: return "信息";
                case DiagnosticLogLevel.Warn: return "警告";
                case DiagnosticLogLevel.Error: return "错误";
                default: throw new ArgumentOutOfRangeException(nameof(level));
            }
        }

        public static bool TryParse(string value, out DiagnosticLogLevel level)
        {
            switch (value)
            {
                case "DEBUG": level = DiagnosticLogLevel.Debug; return true;
                case "INFO": level = DiagnosticLogLevel.Info; return true;
                case "WARN": case "WARNING": level = DiagnosticLogLevel.Warn; return true;
                case "ERROR": level = DiagnosticLogLevel.Error; return true;
                default: level = DiagnosticLogLevel.Info; return false;
            }
        }

        public static DiagnosticLogLevel Parse(string value) => TryParse(value, out var level)
            ? level : throw new ArgumentException("日志等级无效。", nameof(value));
    }
}
