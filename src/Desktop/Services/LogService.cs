using System;
using System.Globalization;
using System.Text.RegularExpressions;
using System.Threading;
using NLog;
using T7.Rekindle.Core;

namespace T7.Rekindle.Desktop.Services
{
    public sealed class LogService
    {
        private static readonly Regex RecordPattern = new Regex(
            @"\A(?<timestamp>[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})  " +
            @"(?<level>DEBUG|INFO|WARN|WARNING|ERROR)  \[(?<source>[^\]\r\n]+)\] (?<message>.*)\z",
            RegexOptions.CultureInvariant | RegexOptions.Singleline);
        private static int _minimumLevel = (int)DiagnosticLogLevel.Info;
        private readonly Logger _logger = LogManager.GetCurrentClassLogger();

        public static DiagnosticLogLevel MinimumLevel => (DiagnosticLogLevel)Volatile.Read(ref _minimumLevel);

        public static void SetMinimumLevel(DiagnosticLogLevel level)
        {
            var name = DiagnosticLogLevels.Name(level);
            var configuration = LogManager.Configuration;
            if (configuration != null)
            {
                configuration.Variables["minimumLogLevel"] = name;
                LogManager.ReconfigExistingLoggers();
            }
            Volatile.Write(ref _minimumLevel, (int)level);
        }

        internal string WriteRecord(string text, Exception error = null)
        {
            var match = RecordPattern.Match(text);
            if (!match.Success || !DateTime.TryParseExact(match.Groups["timestamp"].Value,
                "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var timestamp))
                return Write(DiagnosticLogLevel.Info, text, error, _logger.Name);

            return Write(DiagnosticLogLevels.Parse(match.Groups["level"].Value), match.Groups["message"].Value,
                error, match.Groups["source"].Value, timestamp);
        }

        internal string Write(DiagnosticLogLevel level, string message, Exception error = null,
            string source = "Launcher", DateTime? timestamp = null)
        {
            if (level < MinimumLevel) return null;
            var time = timestamp ?? DateTime.Now;
            var logger = LogManager.GetLogger(source);
            logger.Log(new LogEventInfo(LogLevel.FromString(DiagnosticLogLevels.Name(level)), source, message)
            {
                TimeStamp = time,
                Exception = error
            });
            return time.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) + "  "
                + DiagnosticLogLevels.DisplayName(level) + "  [" + source + "] " + message;
        }

        public void Debug(string message) => Write(DiagnosticLogLevel.Debug, message, source: _logger.Name);
        public void Info(string message) => Write(DiagnosticLogLevel.Info, message, source: _logger.Name);
        public void Warn(string message) => Write(DiagnosticLogLevel.Warn, message, source: _logger.Name);
        public void Error(string message, Exception error) => Write(DiagnosticLogLevel.Error, message, error, _logger.Name);
    }
}
