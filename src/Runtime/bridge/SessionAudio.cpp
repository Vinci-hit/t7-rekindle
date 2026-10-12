#include "Session.h"

namespace t7::bridge {
namespace {
const char* audioState(uint32_t state) {
    switch (state) {
    case T7NB_AUDIO_READY: return "ready";
    case T7NB_AUDIO_APPLYING: return "applying";
    case T7NB_AUDIO_CONFLICT: return "conflict";
    case T7NB_AUDIO_FAILED: return "failed";
    default: return "unavailable";
    }
}
std::string describeSnapshot(const AudioSnapshot& snapshot) {
    return std::string("state=") + audioState(snapshot.state) + "; revision=" + std::to_string(snapshot.revision);
}
}
int32_t Session::audio(AudioSnapshot& result) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7NB_INVALID_HANDLE;
    result = audio_;
    if (snapshot_.state != T7NB_STATE_RUNNING || snapshot_.operation != T7NB_OPERATION_NONE)
        result.state = T7NB_AUDIO_UNAVAILABLE;
    return T7NB_OK;
}
int32_t Session::applyAudio(uint64_t revision, const AudioValues& values) {
    if (!validAudioValues(values)) return T7NB_INVALID_ARGUMENT;
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7NB_INVALID_HANDLE;
    if (snapshot_.state != T7NB_STATE_RUNNING || snapshot_.operation != T7NB_OPERATION_NONE
        || audio_.state == T7NB_AUDIO_UNAVAILABLE || audio_.state == T7NB_AUDIO_FAILED)
        return T7NB_NOT_READY;
    if (audio_.state == T7NB_AUDIO_APPLYING || audioQueued_) return T7NB_BUSY;
    if (revision != audio_.revision) return T7NB_NOT_READY;
    requestedAudio_ = values; requestedAudioRevision_ = revision;
    audioQueued_ = true; audio_.state = T7NB_AUDIO_APPLYING;
    return T7NB_OK;
}
void Session::monitorAudio() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (audio_.state == T7NB_AUDIO_FAILED) return;
    }
    try {
        AudioValues values; uint32_t result = 0; bool applied = false;
        if (!bootstrap_.pollAudio(values, result, applied)) return;
        bool submit = false, changed = false;
        AudioValues request;
        AudioSnapshot observed;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            const auto previous = audio_;
            if (result == 0) {
                audio_.state = applied ? T7NB_AUDIO_FAILED : T7NB_AUDIO_UNAVAILABLE;
                audioQueued_ = false;
            } else {
                if (!sameAudioValues(audio_.values, values) || audio_.state == T7NB_AUDIO_UNAVAILABLE) {
                    audio_.values = values; ++audio_.revision;
                }
                if (audioQueued_) {
                    submit = requestedAudioRevision_ == audio_.revision;
                    request = requestedAudio_; audioQueued_ = false;
                    audio_.state = submit ? T7NB_AUDIO_APPLYING : T7NB_AUDIO_CONFLICT;
                } else if (applied) {
                    audio_.state = result == 2 ? T7NB_AUDIO_CONFLICT : T7NB_AUDIO_READY;
                } else if (audio_.state != T7NB_AUDIO_CONFLICT) {
                    audio_.state = T7NB_AUDIO_READY;
                }
            }
            observed = audio_;
            changed = previous.state != audio_.state || previous.revision != audio_.revision;
        }
        if (changed || applied) log("Audio settings snapshot; " + describeSnapshot(observed)
            + "; mailboxResult=" + std::to_string(result) + "; applied=" + std::to_string(applied)
            + (result == 0 ? "; values=not-read" : "; " + describeAudioValues(values)), DiagnosticLogLevel::Debug);
        if (applied && result == 0) log("Audio settings application was not confirmed by the client", DiagnosticLogLevel::Warn);
        if (result == 0) return;
        if (submit) {
            log("Audio settings apply requested; revision=" + std::to_string(observed.revision) + "; " + describeAudioValues(request), DiagnosticLogLevel::Debug);
            bootstrap_.applyAudio(request);
        }
        if (applied && result == 1) log("Audio settings applied on client frame thread and read back");
    } catch (const std::exception& error) {
        AudioSnapshot last;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            last = audio_;
            audio_.state = T7NB_AUDIO_FAILED; audioQueued_ = false;
        }
        log("Audio settings last snapshot; " + describeSnapshot(last)
            + (last.state == T7NB_AUDIO_UNAVAILABLE ? "; values=not-current" : "; " + describeAudioValues(last.values)), DiagnosticLogLevel::Debug);
        log(std::string("Audio synchronization stopped: ") + error.what()
            + "; scope=settings-mailbox; playbackStatus=not-observed", DiagnosticLogLevel::Warn);
    }
}
}
