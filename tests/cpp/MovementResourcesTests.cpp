#include "../../src/Runtime/launcher/MovementResources.h"
#include "../../src/Runtime/core/Common.h"
#include "../../src/Runtime/launcher/ClientResourceXml.h"
#include "../../src/Runtime/launcher/SprintHookCode.h"
#include "MovementTreeFixture.h"
#include <functional>
#include <iostream>
#include <set>

namespace {
void require(bool valid, const char* message) {
    if (!valid) throw std::runtime_error(message);
}
std::string gbk(const wchar_t* text) {
    const auto size = WideCharToMultiByte(936, WC_NO_BEST_FIT_CHARS, text, -1, nullptr, 0, nullptr, nullptr);
    std::string result(static_cast<size_t>(size), '\0');
    require(size && WideCharToMultiByte(936, WC_NO_BEST_FIT_CHARS, text, -1,
            result.data(), size, nullptr, nullptr) == size, "fixture encoding");
    result.pop_back(); return result;
}
void rejected(t7r::MovementResource resource, const std::string& source) {
    bool failed = false;
    try { t7r::transformMovementResource(resource, source); }
    catch (const std::runtime_error&) { failed = true; }
    require(failed, "changed resource structure was accepted");
}

std::vector<std::string> inputActions(const std::string& source, int playerState,
        bool local, bool battle, const std::string& key, bool pressed,
        const std::string& eventOverride = {}, bool jumpAvailable = true) {
    using namespace t7r::resourceXml;
    const auto nodes = elements(source);
    std::vector<std::string> actions;
    std::function<bool(size_t)> execute = [&](size_t index) {
        const auto& node = nodes[index];
        const auto type = attribute(node, "Type"), name = attribute(node, "Name");
        if (type == "ACTION") {
            if (name == gbk(L"动画系统事件")) {
                const auto param = unique(nodes, index, "Param", "Name", gbk(L"事件名"));
                require(attribute(nodes[param], "Type") == "str", "animation event parameter type");
                actions.push_back("animation:" + attribute(nodes[param], "Value"));
            } else actions.push_back(name);
            return name != gbk(L"跳跃") || jumpAvailable;
        }
        if (type == "CONDITION") {
            if (name == gbk(L"是否为本地单位")) return local;
            if (name == gbk(L"是否为战斗角色")) return battle;
            if (name == gbk(L"本地玩家状态")) {
                const auto param = unique(nodes, index, "Param", "Name", gbk(L"玩家当前状态"));
                require(attribute(nodes[param], "Type") == "enum", "player state parameter type");
                return playerState == std::stoi(attribute(nodes[param], "Value"));
            }
            require(name == gbk(L"按键被按下") || name == gbk(L"按键被松开"), "unknown input condition");
            const auto param = unique(nodes, index, "Param", "Name", gbk(L"按键"));
            return key == attribute(nodes[param], "Value") && pressed == (name == gbk(L"按键被按下"));
        }
        require(type == "SEL" || type == "SEQ", "unknown input node type");
        for (size_t child = index + 1; child < nodes.size(); ++child) {
            if (nodes[child].parent != index || nodes[child].name != "Node") continue;
            const auto result = execute(child);
            if (type == "SEQ" && !result) return false;
            if (type == "SEL" && result) return true;
        }
        return type == "SEQ";
    };
    const auto event = eventOverride.empty() ? (pressed ? "GeEventKeyDown" : "GeEventKeyUp") : eventOverride;
    for (size_t i = 0; i < nodes.size(); ++i)
        if (attribute(nodes[i], "Event").find(event) != std::string::npos) execute(i);
    return actions;
}
}

bool verifyMovementResources() {
    using Resource = t7r::MovementResource;
    try {
        const auto collision = gbk(L"<Node Type=\"ACTION\" ID=\"5\" Name=\"开关物理碰撞\"><Param Name=\"是否开启\" Value=\"false\"/></Node>");
        const auto deathPrefix = gbk(L"<BTree Version=\"4\"><Node ID=\"1\"><Node Description=\"进入节点\"><Node Type=\"SEQ\" Description=\"进入死亡\">");
        const auto delay = "<Node Type=\"DELAY\" ID=\"4\"><DelayTime Value=\"3000\"/></Node>";
        const auto deathSuffix = "</Node></Node></Node></BTree>";
        const auto death = t7r::transformMovementResource(Resource::MonsterDeath,
            deathPrefix + delay + collision + deathSuffix);
        require(death == deathPrefix + collision + delay + deathSuffix,
                "corpse collision is disabled only after death delays");
        require(t7r::transformMovementResource(Resource::MonsterDeath, death) == death,
                "death collision transformation is not idempotent");
        require(t7r::movementResourceForPath(t7r::movementResourcePath(Resource::MonsterDeath)) == Resource::MonsterDeath,
                "monster death resource is not intercepted");
        std::string blood = "<PropertySheet Version=\"100\"><Header/>";
        for (const auto* family : {L"双手刀", L"巨锤", L"长柄刀", L"单手剑", L"长枪",
                L"单手锤", L"双手剑", L"匕首"})
            for (const auto* direction : {L"左砍攻击", L"右砍攻击", L"劈攻击", L"刺攻击"})
                blood += gbk(L"<Record><攻击方类型 Value=\"") + gbk(family)
                    + gbk(L"\"/><攻击方攻击类型 Value=\"") + gbk(direction)
                    + gbk(L"\"/><本地特效实体 Value=\"original-effect\"/></Record>");
        blood += gbk(L"<Record><攻击方类型 Value=\"投枪投掷\"/><攻击方攻击类型 Value=\"\"/><本地特效实体 Value=\"original-projectile\"/></Record></PropertySheet>");
        const auto aliased = t7r::transformMovementResource(Resource::MonsterBlood, blood);
        const auto records = t7r::resourceXml::elements(aliased);
        std::map<std::string, size_t> covered;
        for (const auto& node : records)
            if (node.name == gbk(L"攻击方类型")) ++covered[t7r::resourceXml::attribute(node, "Value")];
        for (const auto* family : {L"大刀", L"狼牙棒", L"偃月刀", L"罗马短剑", L"长矛", L"罗马长矛",
                L"长棍", L"骨朵", L"盾", L"拳头", L"双拳", L"右拳", L"槊", L"双矛", L"双矛左手",
                L"力士锤", L"力士剑", L"力士锤单手", L"力士剑单手", L"袖刃", L"罗马长矛单手",
                L"投枪近战双手", L"罗马投矛双手", L"罗马投矛单手", L"长柄刀大", L"偃月刀大", L"新双手刀"})
            require(covered[gbk(family)] == 4, "missing weapon blood direction");
        require(covered[gbk(L"罗马投矛投掷")] == 1, "missing ranged weapon blood effect");
        require(aliased.find(blood.substr(0, blood.find("</PropertySheet>"))) == 0,
                "existing blood effects changed");
        require(t7r::transformMovementResource(Resource::MonsterBlood, aliased) == aliased,
                "blood rows duplicated on repeated transformation");
        require(t7r::movementResourceForPath(t7r::movementResourcePath(Resource::MonsterBlood)) == Resource::MonsterBlood,
                "monster blood resource is not intercepted");
        rejected(Resource::MonsterBlood, "<PropertySheet Version=\"100\"><Header/></PropertySheet>");
        const auto component = "<GeServerMovable><BTree Value=\"original\"/></GeServerMovable>";
        const auto physics = "<Collision Shape=\"fixture-shape\" Mask=\"7\"/><Camera Value=\"keep\"/>";
        const auto source = std::string("<?xml version=\"1.0\" encoding=\"gb2312\"?><EntSheet><Header>")
            + "<GeServerMovable><BTree Value=\"*.*\"/></GeServerMovable></Header>"
            + gbk(L"<Entity Name=\"武将\">") + component + physics + "</Entity>"
            + gbk(L"<Entity Name=\"女武将\">") + component + physics + "</Entity>"
            + "<Entity Name=\"npc\">" + component + physics + "</Entity></EntSheet>";
        const auto changed = t7r::transformMovementResource(Resource::Infantry, source);
        require(changed.find("<GeOfflineMovable><BTree Value=\"*.*\"/></GeOfflineMovable>") != std::string::npos,
                "offline schema was not derived from the original movable schema");
        require(changed.find(std::string("<Entity Name=\"npc\">") + component + physics + "</Entity>") != std::string::npos,
                "non-hero components changed");
        const auto replacement = "<GeOfflineMovable><BTree Value=\"" + t7r::movementResourcePath(Resource::Router)
            + "\"/></GeOfflineMovable>";
        for (auto name : {L"武将", L"女武将"})
            require(changed.find("<Entity Name=\"" + gbk(name) + "\">" + replacement + physics + "</Entity>") != std::string::npos,
                    "hero collision/camera components changed");
        rejected(Resource::Infantry, changed);
        rejected(Resource::Infantry, source.substr(0, source.size() - 1));
        rejected(Resource::Infantry, "<EntSheet><Header/></EntSheet>");
        rejected(Resource::Infantry, "<!DOCTYPE EntSheet><EntSheet/>");

        const auto birth = gbk(L"<BTree Version=\"4\"><Node Type=\"SEL\" ID=\"1\">"
            L"<Node Type=\"SEL\" ID=\"2\"><Node Type=\"SEQ\" ID=\"20\">"
            L"<Node Type=\"ACTION\" ID=\"21\" Name=\"设置步兵转向\"/>"
            L"<Node Type=\"ACTION\" ID=\"22\" Name=\"fixture-camera\"/>"
            L"</Node></Node><Node Type=\"SEL\" ID=\"3\"/></Node></BTree>");
        const auto turned = t7r::transformMovementResource(Resource::Birth, birth);
        require(turned.find(gbk(L"Name=\"设置离线步兵转向\"")) != std::string::npos,
                "offline turn action missing");
        require(turned.find(gbk(L"Name=\"设置步兵转向\"")) != std::string::npos,
                "online turn fallback missing");
        require(turned.find("<Node Type=\"ACTION\" ID=\"22\" Name=\"fixture-camera\"/>") != std::string::npos,
                "birth camera action changed");
        for (auto name : {L"是否为本地单位", L"是否为战斗角色", L"步兵待机"})
            require(turned.find(gbk(name)) != std::string::npos, "birth scope or turn parameters missing");
        rejected(Resource::Birth, turned);
        rejected(Resource::Birth, "<BTree><Node ID=\"1\"/><Node ID=\"1\"/></BTree>");

        const auto movement = gbk(movementTreeFixture());
        const auto router = t7r::transformMovementResource(Resource::Router, movement);
        auto withSpeed = movement;
        const auto moveNode = std::string("<Node Type=\"ACTION\" ID=\"13\" Name=\"fixture-move\"/>");
        withSpeed.replace(withSpeed.find(moveNode), moveNode.size(), gbk(
            L"<Node Type=\"ACTION\" ID=\"13\" Name=\"直线移动\">"
            L"<Param Name=\"方向\" Type=\"str\" Value=\"up\"/>"
            L"<Param Name=\"速度\" Type=\"f32\" Value=\"5.0\"/>"
            L"<Param Name=\"最大速度\" Type=\"f32\" Value=\"25.0\"/>"
            L"<Param Name=\"加速度\" Type=\"f32\" Value=\"0.0\"/></Node>"));
        const auto jog = t7r::transformMovementResource(Resource::Router, withSpeed);
        require(jog.find(gbk(L"Name=\"速度\" Type=\"f32\" Value=\"") + std::to_string(t7r::JOG_SPEED) + "\"") != jog.npos,
                "movement key-down did not start at the offline jog speed");
        require(jog.find(gbk(L"Name=\"加速度\" Type=\"f32\" Value=\"0.0\"")) != jog.npos,
                "jog changed the original acceleration command");
        const auto lifecycle = gbk(L"<Node Type=\"SEL\" ID=\"1\">"
            L"<Node Type=\"SEL\" ID=\"2\" Description=\"进入节点\"/>"
            L"<Node Type=\"SEL\" ID=\"3\" Description=\"退出节点\"/>"
            L"<Node Type=\"SEL\" ID=\"4\" Description=\"执行节点\">");
        require(router.find(lifecycle) != std::string::npos,
                "router lifecycle selectors need GBK Description labels, not just IDs");
        for (const auto& key : {"W", "Space", "Ctrl"}) {
            for (int state : {0, 1, 3})
                require(inputActions(router, state, true, true, key, true).empty(),
                        "movement input ran outside preparation or combat");
            require(inputActions(router, 4, false, true, key, true).empty(), "remote actor accepted local input");
            require(inputActions(router, 4, true, false, key, true).empty(), "non-battle actor accepted input");
        }
        for (const auto& key : {"Space", "Ctrl"})
            require(inputActions(router, 2, true, true, key, true).empty(), "preparation accepted a non-movement key");
        for (const auto& key : {"W", "A", "S", "D"}) {
            auto keyTree = movement;
            const std::string original = "Value=\"W\"", replacementKey = std::string("Value=\"") + key + "\"";
            for (size_t pos = 0; (pos = keyTree.find(original, pos)) != keyTree.npos; pos += replacementKey.size())
                keyTree.replace(pos, original.size(), replacementKey);
            const auto routed = t7r::transformMovementResource(Resource::Router, keyTree);
            for (bool pressed : {false, true}) {
                const std::vector<std::string> expected{pressed ? "fixture-move" : "fixture-stop"};
                require(inputActions(routed, 2, true, true, key, pressed) == expected,
                        "preparation discarded the native movement direction or release");
                require(inputActions(routed, 4, true, true, key, pressed) == expected,
                        "combat changed native movement key handling");
                require(inputActions(routed, 2, false, true, key, pressed).empty(), "remote preparation accepted input");
                require(inputActions(routed, 2, true, false, key, pressed).empty(), "non-battle preparation accepted input");
            }
            require(inputActions(routed, 4, true, true, "", false, "PLAYER_START_FIGHT").empty(),
                    "battle entry replayed a movement command");
        }
        require(inputActions(router, 4, true, true, "W", true) == std::vector<std::string>{"fixture-move"},
                "first combat movement input changed");
        require(inputActions(router, 4, true, true, "Space", true)
                    == std::vector<std::string>{gbk(L"跳跃")},
                "queueing a jump must not animate a rejected or airborne jump");
        require(inputActions(router, 4, true, true, "", false, "JUMPING").empty(),
                "jump animation must have one owner: the native physical-state observer");
        for (const auto& event : {"JUMPING", "JUMP_LAND"}) {
            require(inputActions(router, 2, true, true, "", false, event).empty(), "pre-game jump event escaped gates");
            require(inputActions(router, 4, false, true, "", false, event).empty(), "remote jump event escaped gates");
            require(inputActions(router, 4, true, false, "", false, event).empty(), "non-battle jump event escaped gates");
        }
        require(router.find("Value=\"MoveDir\"") == std::string::npos,
                "key events must not compete with the native locomotion animation writer");
        require(inputActions(router, 4, true, true, "Space", false).empty(), "jump release replayed animation");
        require(inputActions(router, 4, true, true, "Space", true, {}, false) == std::vector<std::string>{gbk(L"跳跃")},
                "jump animation ran after the original jump action failed");
        require(inputActions(router, 4, true, true, "Ctrl", true) == std::vector<std::string>{"animation:Crouch"},
                "crouching still waits for a server movement response");
        require(inputActions(router, 2, true, true, "Ctrl", false).empty(), "preparation allowed standing input");
        require(inputActions(router, 4, true, true, "Ctrl", false) == std::vector<std::string>{"animation:EndCrouch"},
                "combat standing input was lost");
        require(inputActions(router, 4, true, true, "", false, "JUMP_LAND")
                    == std::vector<std::string>{"fixture-land"},
                "landing sound must remain without replaying the native jump observer's animation");
        for (const auto& event : {"Jump", "EndJump"})
            require(router.find(std::string("Value=\"") + event + "\"") == std::string::npos,
                    "jump animation still has a competing behavior-tree writer");
        require(router.find("Value=\"JumpLand\"") == std::string::npos,
                "landing notification must not re-enter a persistent landing pose");
        require(inputActions(router, 2, true, true, "", false, "JUMP_LAND").empty(),
                "landing animation escaped the game phase gate");
        require(inputActions(router, 4, false, true, "", false, "JUMP_LAND").empty(),
                "remote actor accepted a local end-jump event");
        require(inputActions(router, 4, true, false, "", false, "JUMP_LAND").empty(),
                "non-battle actor accepted an end-jump event");
        require(router.find(gbk(L"请求进入下蹲")) == std::string::npos
                    && router.find(gbk(L"请求离开下蹲")) == std::string::npos,
                "offline crouch still dispatches online requests");
        require(router.find("Name=\"fixture-land\"") != std::string::npos, "original landing action was lost");
        require(router.find(gbk(L"切换行为树")) == std::string::npos, "router can switch into an ungated movement tree");
        std::set<std::string> ids;
        for (const auto& node : t7r::resourceXml::elements(router))
            if (node.name == "Node") require(ids.insert(t7r::resourceXml::attribute(node, "ID")).second, "duplicate movement node ID");
        rejected(Resource::Router, router);
        rejected(Resource::Router, "<BTree Version=\"4\"/>");
        for (const auto* action : {L"跳跃", L"请求进入下蹲", L"请求离开下蹲"}) {
            auto missing = movement;
            const auto name = gbk(action);
            missing.replace(missing.find(name), name.size(), "unsupported-action");
            rejected(Resource::Router, missing);
        }
        auto noLanding = movement;
        noLanding.replace(noLanding.find("JUMP_LAND"), std::string("JUMP_LAND").size(), "OTHER_EVENT");
        rejected(Resource::Router, noLanding);
        auto duplicateJump = movement;
        duplicateJump.replace(duplicateJump.find("fixture-move"), std::string("fixture-move").size(), gbk(L"跳跃"));
        rejected(Resource::Router, duplicateJump);
        auto exhaustedIds = movement;
        exhaustedIds.replace(exhaustedIds.find("ID=\"24\""), std::string("ID=\"24\"").size(), "ID=\"2147483640\"");
        rejected(Resource::Router, exhaustedIds);
        require(t7r::movementResourceForPath("..\\DATA\\BTREE\\T7R_RUNTIME_MOVEMENT.BTREE") == Resource::Router,
                "resource path normalization failed");
        require(t7r::movementResourceForPath(t7r::movementOfflineTreePath()) == Resource::None,
                "original movement tree must remain unchanged");
        require(t7r::transformMovementResource(Resource::None, "not XML") == "not XML", "unrelated data changed");
        rejected(Resource::Router, std::string(t7r::MOVEMENT_XML_LIMIT + 1, 'x'));
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Movement resources: " << error.what() << '\n'; return false;
    }
}
