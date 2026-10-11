#include "../../src/Runtime/server/Server.h"
#include <initializer_list>
#include <iostream>
#include <utility>

namespace t7 {
struct ServerEventOrderTests {
    using Item = std::pair<std::string, std::string>;

    static void initialize(Server& server) {
        server.signals_ = std::make_unique<detail::ServerSignals>();
        server.stopping_ = false;
    }

    static void enqueue(Server& server, const Item& item) {
        Event event; event.type = item.first; event.connection = 1;
        event.body = {1, 2, 3}; event.serialized = item.second;
        server.queue(std::move(event));
        server.events_.back().timerId = item.second;
    }

    static bool checkOrder(std::initializer_list<Item> items, std::initializer_list<uint64_t> expected) {
        Server server; initialize(server);
        Server::Timers timers;
        for (const auto& item : items) enqueue(server, item);
        for (auto id : expected) {
            if (server.events_.empty()) return false;
            Server::QueuedEvent event; std::vector<uint64_t> live;
            if (!server.nextEvent(timers, event, live) || event.id != id) return false;
            size_t bytes = 0;
            for (const auto& queued : server.events_)
                bytes += queued.body.size() + queued.serialized.size() + 256;
            if (server.eventBytes_ != bytes) return false;
        }
        return server.events_.empty() && server.eventBytes_ == 0;
    }

    static bool verify(const fs::path& fixtureRoot) {
        const Item decision{"timer", "1:actors-decision"}, pulse{"timer", "1:actors-pulse"};
        const Item action{"timer", "1:battle-action-phase"}, hurt{"timer", "1:combat-hurt-1"};
        const Item input{"message", ""};
        for (const auto& barrier : {input, Item{"world", ""}, Item{"control", ""}, Item{"operator", ""},
                Item{"connected", ""}, Item{"authenticated", ""}, Item{"closed", ""},
                Item{"timer", "1:version"}, Item{"timer", "1:bot-move"},
                Item{"timer", "1:actors-decision-extra"}}) {
            if (!checkOrder({barrier, hurt}, {1, 2})
                || !checkOrder({decision, barrier, action}, {1, 2, 3})) {
                std::cerr << "animation timer overtook a causal barrier: " << barrier.first << ' ' << barrier.second << '\n';
                return false;
            }
        }
        for (const auto* id : {"1:battle-action-phase", "1:bot-action-phase-20", "1:bot-release", "1:combat-hurt-2"}) {
            if (!checkOrder({decision, pulse, {"timer", "2:bot-think-20"}, {"timer", id}}, {4, 1, 2, 3})) {
                std::cerr << "animation timer did not overtake consecutive decisions\n";
                return false;
            }
        }
        if (!checkOrder({input, decision, pulse, action, hurt, input}, {1, 4, 5, 2, 3, 6})
            || !checkOrder({action, decision, hurt}, {1, 3, 2})
            || !checkOrder({decision, pulse}, {1, 2})
            || !checkOrder({input, input}, {1, 2})) {
            std::cerr << "event queue lost stable ordering\n";
            return false;
        }

        // Simulate a blocked callback: input is queued before the timer expires.
        for (const auto* id : {"1:combat-hurt-1", "1:battle-action-phase"}) {
            Server server; initialize(server);
            enqueue(server, input);
            Server::Timers timers{{id, {detail::Clock::now(), 1, "expired"}}};
            Server::QueuedEvent event; std::vector<uint64_t> live;
            if (!server.nextEvent(timers, event, live) || event.type != "message"
                || !timers.empty() || server.events_.size() != 1 || server.events_.front().timerId != id) {
                std::cerr << "expired timer ran before previously queued input\n";
                return false;
            }
            if (id == hurt.second) {
                if (!server.nextEvent(timers, event, live) || event.timerId != id || server.eventBytes_ != 0) {
                    std::cerr << "hurt recovery did not follow the queued attack\n";
                    return false;
                }
            } else {
                server.journal_ = std::make_unique<Journal>(fixtureRoot / "event-order-journal");
                Transition result; result.state = "{}";
                result.timers.push_back({id, -1, "", 1});
                std::string state; bool committed = false;
                server.commit(result, event, "test", "", detail::Clock::now(), 0, state, timers, committed);
                if (!committed || !server.events_.empty() || server.eventBytes_ != 0 || !timers.empty()) {
                    std::cerr << "release input failed to cancel an already queued action timer\n";
                    return false;
                }
            }
        }
        return true;
    }
};
}

bool verifyServerEventOrder(const t7::fs::path& fixtureRoot) {
    return t7::ServerEventOrderTests::verify(fixtureRoot);
}
