#include "../../src/Runtime/bridge/Session.h"
#include <atomic>
#include <chrono>
#include <future>
#include <iostream>
#include <thread>

namespace {
struct CompletionGate {
    std::weak_ptr<t7r::bridge::Session> session;
    std::promise<void> reached;
    std::promise<void> resume;
    std::shared_future<void> resumed = resume.get_future().share();
    std::atomic<bool> timedOut{false};
    bool firstCheck = true;
    bool firstCompletion = true;

    void pause() {
        if (!firstCompletion) return;
        firstCompletion = false;
        reached.set_value();
        timedOut = resumed.wait_for(std::chrono::seconds(5)) != std::future_status::ready;
    }
};
}

bool verifyOperationPublication(const t7r::fs::path& packageRoot, uint32_t expectedStatus) {
    auto gate = std::make_shared<CompletionGate>();
    auto reached = gate->reached.get_future();
    t7r::Bootstrap::TestAdapter adapter;
    adapter.check = [gate, expectedStatus](const t7r::fs::path&, const t7r::Config&,
                                          const t7r::DiagnosticLogCallback&) {
        if (!gate->firstCheck) return;
        gate->firstCheck = false;
        if (expectedStatus == T7RNB_OPERATION_FAILED)
            throw std::runtime_error("synthetic preflight failure");
        if (expectedStatus == T7RNB_OPERATION_CANCELLED) {
            auto session = gate->session.lock();
            t7r::bridge::Snapshot snapshot;
            session->snapshot(snapshot);
            if (session->cancel(snapshot.operationId) != T7RNB_OK)
                throw std::runtime_error("test cancellation was not accepted");
        }
    };
    auto session = t7r::bridge::Session::createForTest(t7r::utf8(packageRoot.wstring()),
        std::move(adapter), [gate] { gate->pause(); });
    gate->session = session;
    const auto directory = t7r::utf8((packageRoot / "synthetic-client" / "Bin").wstring());
    uint64_t operationId = 0;
    bool valid = session->submit(T7RNB_OPERATION_CHECK, directory, operationId) == T7RNB_OK;
    valid = reached.wait_for(std::chrono::seconds(5)) == std::future_status::ready && valid;

    // Hold the worker after it publishes the result: callers must already be
    // able to submit another operation without waiting for a later iteration.
    t7r::bridge::Operation operation;
    t7r::bridge::Snapshot snapshot;
    valid = session->operation(operationId, operation) == T7RNB_OK && valid;
    valid = session->snapshot(snapshot) == T7RNB_OK && valid;
    valid = valid && operation.status == expectedStatus && snapshot.operation == T7RNB_OPERATION_NONE
        && snapshot.operationId == operationId && (snapshot.flags & 1u)
        && snapshot.state == (expectedStatus == T7RNB_OPERATION_FAILED ? T7RNB_STATE_FAILED : T7RNB_STATE_IDLE)
        && session->cancel(operationId) == T7RNB_NOT_READY;
    uint64_t nextId = 0;
    const auto nextStatus = session->submit(T7RNB_OPERATION_CHECK, directory, nextId);
    valid = nextStatus == T7RNB_OK && nextId > operationId && valid;
    t7r::bridge::Snapshot queued;
    session->snapshot(queued);
    valid = valid && queued.operation == T7RNB_OPERATION_CHECK && queued.operationId == nextId
        && queued.state == T7RNB_STATE_CHECKING && (queued.flags & 1u) == 0;
    gate->resume.set_value();

    if (nextStatus == T7RNB_OK) {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        t7r::bridge::Operation next;
        do {
            session->operation(nextId, next);
            if (next.status >= T7RNB_OPERATION_SUCCEEDED) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        } while (std::chrono::steady_clock::now() < deadline);
        session->snapshot(queued);
        valid = valid && next.status == T7RNB_OPERATION_SUCCEEDED && queued.operation == T7RNB_OPERATION_NONE
            && queued.operationId == nextId && queued.state == T7RNB_STATE_IDLE && (queued.flags & 1u);
    }
    session->requestClose();
    valid = session->waitForWorkerForTest(std::chrono::seconds(5)) && !gate->timedOut && valid;
    if (!valid) std::cerr << "operation publication: expected=" << expectedStatus
        << " status=" << operation.status << " active=" << snapshot.operation
        << " nextSubmit=" << nextStatus << '\n';
    return valid;
}
