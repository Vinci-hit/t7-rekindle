using System;
using System.Linq;
using T7.Rekindle.Core;

namespace T7.BattleHarness
{
    internal static class ActorMapTests
    {
        internal static void Run()
        {
            var roster = Enumerable.Range(1, 20).Select(id =>
                new BattleActorIdentity(id, (ulong)(100 + id), id <= 10 ? 1 : 2)).ToArray();
            foreach (var own in roster)
            {
                var view = new BattleActorMap(7, own.PlayerId, roster);
                Program.Check(view.MatchId == 7 && view.OwnActorId == own.ActorId && view.ToGlobal(1) == own.ActorId, "local identity");
                Program.Check(view.ToLocal(0) == 0 && view.ToGlobal(0) == 0, "terrain target");
                foreach (var actor in roster)
                {
                    int local = view.ToLocal(actor.ActorId);
                    Program.Check(view.ToGlobal(local) == actor.ActorId, "bijective actor mapping");
                    Program.Check(actor.ActorId == own.ActorId ? local == 1
                        : actor.Camp == own.Camp ? local >= 12 && local <= 20 : local >= 2 && local <= 11, "relative camp slots");
                }
                var reordered = new BattleActorMap(7, own.PlayerId, roster.Reverse());
                Program.Check(roster.All(actor => view.ToLocal(actor.ActorId) == reordered.ToLocal(actor.ActorId)), "roster ordering independence");
            }
            var first = new BattleActorMap(7, 101, roster);
            var second = new BattleActorMap(7, 102, roster);
            Program.Check(first.ToLocal(second.ToGlobal(1)) == 12 && second.ToLocal(first.ToGlobal(1)) == 12, "two humans keep distinct global identity");
            Program.Throws<ArgumentException>(() => new BattleActorMap(7, 101, roster.Concat(roster.Take(1))));
            Program.Throws<ArgumentException>(() => new BattleActorMap(7, 101, new[] { roster[0], roster[0] }));
            Program.Throws<ArgumentException>(() => new BattleActorMap(7, 101,
                new[] { roster[0], new BattleActorIdentity(2, 101, 2) }));
            Program.Throws<ArgumentException>(() => new BattleActorMap(7, 101,
                Enumerable.Range(1, 11).Select(id => new BattleActorIdentity(id, (ulong)(100 + id), 1))));
            Program.Throws<ArgumentException>(() => new BattleActorMap(7, 500, roster));
            Program.Throws<ArgumentOutOfRangeException>(() => new BattleActorMap(0, 101, roster));
            Program.Throws<ArgumentOutOfRangeException>(() => new BattleActorIdentity(21, 0, 1));
            Program.Throws<ArgumentOutOfRangeException>(() => new BattleActorIdentity(1, 0, 3));
            var partial = new BattleActorMap(7, 101, new[] { roster[0], new BattleActorIdentity(20, 0, 2) });
            Program.Check(partial.ToLocal(20) == 2, "sparse roster and bot mapping");
            Program.Throws<ArgumentOutOfRangeException>(() => partial.ToGlobal(12));
            Program.Throws<ArgumentOutOfRangeException>(() => partial.ToLocal(2));
        }
    }
}
