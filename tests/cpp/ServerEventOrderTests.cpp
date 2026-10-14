#include "../../src/Runtime/server/Server.h"
#include <initializer_list>
#include <iostream>
#include <utility>

namespace t7r {
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
        {
            Server server; initialize(server);
            WorldSnapshot world;
            server.updateWorld(world);
            const auto empty = server.world_;
            if (!empty || empty->version != 1 || !server.events_.empty()) return false;
            world.ready = true; world.sampledAt = GetTickCount64(); world.instanceStartedAt = 123;
            server.sessions_[1].authenticated = true; server.sessions_[1].role = "instance";
            server.updateWorld(world);
            const auto published = server.world_;
            if (published->version != 2 || !published->snapshot.ready || !server.worldPending_
                || server.events_.size() != 1 || server.events_.front().world != published
                || empty->snapshot.ready) return false;
            bool rejected = false;
            try { server.updateWorld(world); } catch (const std::exception&) { rejected = true; }
            if (!rejected || server.world_ != published) return false;
            Server::Timers timers;
            Server::QueuedEvent event; std::vector<uint64_t> live;
            if (!server.nextEvent(timers, event, live) || event.world != published) return false;
            server.worldPending_ = false;
            server.updateWorld(WorldSnapshot{});
            if (server.world_->version != 3 || server.world_->snapshot.ready || !server.events_.empty()
                || !event.world->snapshot.ready) return false;
            world.sampledAt = GetTickCount64() - 251;
            server.updateWorld(world);
            if (server.world_->version != 4 || server.world_->snapshot.ready || !server.events_.empty()) return false;
        }
        {
            Server server; initialize(server);
            server.journal_ = std::make_unique<Journal>(fixtureRoot / "context-rejection-journal");
            Server::QueuedEvent event; event.type = "message"; event.connection = 1;
            Transition transition; transition.state = "changed";
            transition.send.push_back({1, 4, {1}, "dead-target"});
            transition.timers.push_back({"1:battle-action-phase", 0, "new", 1});
            Server::Timers timers{{"old", {detail::Clock::now(), 1, "old"}}};
            std::string state = "original"; bool committed = false, rejected = false;
            try { server.commit(transition, event, "test", "", detail::Clock::now(), 0, state, timers, committed); }
            catch (const std::exception&) { rejected = true; }
            if (!rejected || committed || state != "original" || timers.size() != 1 || !timers.count("old")) return false;
            server.sessions_[1] = {};
            transition.timers[0].connection = 2;
            rejected = false;
            try { server.commit(transition, event, "test", "", detail::Clock::now(), 0, state, timers, committed); }
            catch (const std::exception&) { rejected = true; }
            if (!rejected || committed || state != "original" || !server.sessions_[1].send.empty()
                || server.sessions_[1].queued != 0 || timers.size() != 1 || !timers.count("old")) return false;
        }
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

bool verifyServerEventOrder(const t7r::fs::path& fixtureRoot) {
    return t7r::ServerEventOrderTests::verify(fixtureRoot);
}
