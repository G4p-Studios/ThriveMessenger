import Foundation
import Network
import Observation

@MainActor @Observable
final class ThriveSession {
    var host = "im.tappedin.fm"
    var username = ""
    var password = ""
    var port = 2005
    var isSignedIn = false
    var isBusy = false
    var isMuted = false
    var isDeafened = false
    var errorMessage: String?
    var contacts: [Contact] = []
    var rooms: [Room] = []
    var openRoom: Room?
    var roomMembers: [RoomMember] = []
    var roomMessages: [ChatMessage] = []
    var directMessages: [String: [ChatMessage]] = [:]
    var incomingCall: IncomingCall?
    var activeCall: ActiveCall?
    private var connection: NWConnection?
    private var buffer = Data()
    private let audio = VoiceAudioService()

    func signIn() async {
        guard !username.isEmpty, !password.isEmpty else { errorMessage = "Enter a username and password."; return }
        isBusy = true; errorMessage = nil
        do {
            let value = NWConnection(host: .init(host), port: .init(rawValue: UInt16(port))!, using: .tls); connection = value
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                value.stateUpdateHandler = { state in
                    switch state {
                    case .ready: value.stateUpdateHandler = nil; continuation.resume()
                    case .failed(let error): value.stateUpdateHandler = nil; continuation.resume(throwing: error)
                    default: break
                    }
                }
                value.start(queue: .global(qos: .userInitiated))
            }
            receiveNext(); try await send(["action": "login", "user": username, "pass": password])
        } catch { errorMessage = error.localizedDescription; disconnect() }
        isBusy = false
    }
    func refreshRooms() async { await safeSend(["action": "group_room_list"]) }
    func open(_ room: Room) async { await safeSend(room.role?.isEmpty == false ? ["action": "group_room_open", "room_id": room.id] : ["action": "group_room_join", "room_id": room.id]) }
    func createRoom(name: String, description: String, visibility: String, expiration: String) async { await safeSend(["action": "group_room_create", "name": name, "description": description, "visibility": visibility, "expiration": expiration]) }
    func leaveRoom() async { guard let room = openRoom else { return }; await safeSend(["action": "group_room_leave", "room_id": room.id]) }
    func sendRoomMessage(_ body: String) async { guard let room = openRoom, !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }; await safeSend(["action": "group_room_message", "room_id": room.id, "body": body]) }
    func sendRoomFile(_ data: Data, filename: String) async { guard let room = openRoom else { return }; await safeSend(["action": "group_room_file", "room_id": room.id, "room_name": room.name, "filename": filename, "data": data.base64EncodedString()]) }
    func sendDirectMessage(to name: String, body: String) async { guard !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }; await safeSend(["action": "msg", "to": name, "from": username, "msg": body]); directMessages[name, default: []].append(.init(sender: username, body: body)) }
    func setRole(_ role: String, member: RoomMember) async { guard let room = openRoom else { return }; await safeSend(["action": "group_room_set_role", "room_id": room.id, "username": member.name, "role": role]) }
    func startCall(with name: String) async { SoundPlayer.play("outgoing_call", loops: -1); await safeSend(["action": "voice_call_request", "to": name]) }
    func answerCall() async { guard let call = incomingCall else { return }; incomingCall = nil; await safeSend(["action": "voice_call_accept", "call_id": call.id]) }
    func declineCall() async { guard let call = incomingCall else { return }; incomingCall = nil; SoundPlayer.stop(); await safeSend(["action": "voice_call_decline", "call_id": call.id]) }
    func joinGroupCall() async { guard let room = openRoom else { return }; await safeSend(["action": "group_call_join", "group": room.name, "mode": "voice"]) }
    func endCall() async { guard let call = activeCall else { return }; await safeSend(call.isGroup ? ["action": "group_call_leave", "group": call.group] : ["action": "voice_call_end", "call_id": call.id]); finishCall("call_ended") }
    func disconnect() { audio.stop(); SoundPlayer.stop(); connection?.cancel(); connection = nil; isSignedIn = false; contacts = []; rooms = []; openRoom = nil; activeCall = nil; incomingCall = nil }

    private func safeSend(_ object: [String: Any]) async { do { try await send(object) } catch { errorMessage = error.localizedDescription } }
    private func send(_ object: [String: Any]) async throws {
        guard let connection else { throw URLError(.notConnectedToInternet) }
        var data = try JSONSerialization.data(withJSONObject: object); data.append(0x0A)
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in connection.send(content: data, completion: .contentProcessed { $0 == nil ? continuation.resume() : continuation.resume(throwing: $0!) }) }
    }
    private func receiveNext() { connection?.receive(minimumIncompleteLength: 1, maximumLength: 1_048_576) { [weak self] data, _, complete, error in guard let self else { return }; Task { @MainActor in if let data { self.buffer.append(data); self.consumeLines() }; if error != nil || complete { self.disconnect() } else { self.receiveNext() } } } }
    private func consumeLines() { while let newline = buffer.firstIndex(of: 0x0A) { let line = buffer[..<newline]; buffer.removeSubrange(...newline); if let value = try? JSONSerialization.jsonObject(with: line) as? [String: Any] { handle(value) } } }
    private func handle(_ json: [String: Any]) {
        if let status = json["status"] as? String { if status == "ok" { isSignedIn = true; password = ""; Task { await refreshRooms() } } else { errorMessage = json["reason"] as? String ?? "Sign in failed." } }
        switch json["action"] as? String {
        case "contact_list": contacts = (json["contacts"] as? [[String: Any]] ?? []).compactMap(Contact.init)
        case "group_room_list_response": rooms = (json["rooms"] as? [[String: Any]] ?? []).compactMap(Room.init)
        case "group_room_open_response":
            guard json["ok"] as? Bool != false, let raw = json["room"] as? [String: Any], let room = Room(raw) else { errorMessage = json["reason"] as? String; return }
            openRoom = room; roomMembers = (json["members"] as? [[String: Any]] ?? []).compactMap(RoomMember.init); roomMessages = (json["messages"] as? [[String: Any]] ?? []).compactMap(ChatMessage.init).reversed()
        case "group_room_result":
            if json["ok"] as? Bool == false { errorMessage = json["reason"] as? String ?? "Room action failed." }
            else if let raw = json["room"] as? [String: Any], let room = Room(raw) { Task { await open(room); await refreshRooms() } }
            else if json["event"] as? String == "left" { openRoom = nil; Task { await refreshRooms() } }
        case "group_room_message", "group_room_file": if let raw = json["message"] as? [String: Any], let message = ChatMessage(raw) { roomMessages.append(message) }
        case "group_room_members": roomMembers = (json["members"] as? [[String: Any]] ?? []).compactMap(RoomMember.init)
        case "msg": let sender = json["from"] as? String ?? "Unknown"; directMessages[sender, default: []].append(.init(sender: sender, body: json["msg"] as? String ?? ""))
        case "voice_call_incoming": incomingCall = .init(id: json["call_id"] as? String ?? "", caller: json["from"] as? String ?? "Unknown"); SoundPlayer.play("incoming_call", loops: -1)
        case "voice_call_event": directCallEvent(json)
        case "group_call_result": groupCallEvent(json)
        case "group_call_audio": if let encoded = json["data"] as? String, let data = Data(base64Encoded: encoded) { audio.play(data, deafened: isDeafened) }
        default: break
        }
    }
    private func directCallEvent(_ json: [String: Any]) { let event = json["event"] as? String ?? ""; if event == "accepted" { let id = json["call_id"] as? String ?? ""; activeCall = .init(id: id, group: "direct:\(id)", title: json["with"] as? String ?? "Voice Call", isGroup: false); beginAudio(sound: "call_connected") } else if ["declined", "ended", "failed"].contains(event) { finishCall("call_ended"); if event == "failed" { errorMessage = json["reason"] as? String ?? "Call failed." } } }
    private func groupCallEvent(_ json: [String: Any]) { guard json["ok"] as? Bool != false else { errorMessage = json["reason"] as? String ?? "Group call failed."; return }; if json["event"] as? String == "joined", let group = json["group"] as? String { activeCall = .init(id: group, group: group, title: group, isGroup: true); beginAudio(sound: "group_call_join") } else if json["event"] as? String == "left" { finishCall("group_call_leave") } }
    private func beginAudio(sound: String) { SoundPlayer.play(sound); audio.onCaptured = { [weak self] data in guard let self else { return }; Task { @MainActor in guard let call = self.activeCall, !self.isMuted else { return }; await self.safeSend(["action": "group_call_audio", "group": call.group, "data": data.base64EncodedString()]) } }; Task { do { try await audio.start() } catch { errorMessage = "Voice audio could not start: \(error.localizedDescription)" } } }
    private func finishCall(_ sound: String) { audio.stop(); SoundPlayer.play(sound); activeCall = nil; incomingCall = nil }
}

struct Contact: Identifiable, Hashable { let name: String, online: Bool; var id: String { name }; init?(_ value: [String: Any]) { guard let name = value["user"] as? String else { return nil }; self.name = name; online = value["online"] as? Bool ?? false } }
struct Room: Identifiable, Hashable { let id, name, description, visibility: String; let role: String?; init?(_ value: [String: Any]) { guard let id = value["room_id"] as? String, let name = value["name"] as? String else { return nil }; self.id = id; self.name = name; description = value["description"] as? String ?? ""; visibility = value["visibility"] as? String ?? "public"; role = value["role"] as? String } }
struct RoomMember: Identifiable, Hashable { let name, role: String; var id: String { name }; init?(_ value: [String: Any]) { guard let name = value["username"] as? String else { return nil }; self.name = name; role = value["role"] as? String ?? "guest" } }
struct ChatMessage: Identifiable, Hashable { let id, sender, body, kind, filename: String; let sentAt: Date; init(sender: String, body: String) { id = UUID().uuidString; self.sender = sender; self.body = body; kind = "text"; filename = ""; sentAt = Date() }; init?(_ value: [String: Any]) { id = value["message_id"] as? String ?? UUID().uuidString; sender = value["sender"] as? String ?? "Unknown"; body = value["body"] as? String ?? ""; kind = value["kind"] as? String ?? "text"; filename = value["filename"] as? String ?? ""; sentAt = Date(timeIntervalSince1970: value["sent_at"] as? Double ?? Date().timeIntervalSince1970) } }
struct IncomingCall: Identifiable { let id, caller: String }
struct ActiveCall: Identifiable { let id, group, title: String; let isGroup: Bool }
