import SwiftUI
import UniformTypeIdentifiers

struct LoginView: View {
    @Bindable var session: ThriveSession
    var body: some View { NavigationStack { Form {
        Section("Server") { TextField("Host", text: $session.host).textContentType(.URL).textInputAutocapitalization(.never); TextField("Port", value: $session.port, format: .number).keyboardType(.numberPad) }
        Section("Account") { TextField("Username", text: $session.username).textContentType(.username).textInputAutocapitalization(.never); SecureField("Password", text: $session.password).textContentType(.password) }
        if let error = session.errorMessage { Text(error).foregroundStyle(.red).accessibilityLabel("Sign in error: \(error)") }
        Button(session.isBusy ? "Signing In…" : "Sign In") { Task { await session.signIn() } }.disabled(session.isBusy).accessibilityHint("Connects securely to the selected Thrive server")
    }.navigationTitle("Thrive Messenger") } }
}

struct ContactsView: View {
    @Bindable var session: ThriveSession
    var body: some View { List(session.contacts) { contact in NavigationLink { DirectChatView(session: session, contact: contact.name) } label: { Label(contact.name, systemImage: contact.online ? "circle.fill" : "circle").accessibilityLabel("\(contact.name), \(contact.online ? "online" : "offline")") } }.navigationTitle("Contacts").overlay { if session.contacts.isEmpty { ContentUnavailableView("No Contacts", systemImage: "person.2") } } }
}

struct DirectChatView: View {
    @Bindable var session: ThriveSession; let contact: String; @State private var draft = ""
    var body: some View { VStack {
        MessageList(messages: session.directMessages[contact] ?? [])
        HStack { TextField("Message", text: $draft, axis: .vertical).textFieldStyle(.roundedBorder); Button("Send") { let value = draft; draft = ""; Task { await session.sendDirectMessage(to: contact, body: value) } }.disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
        .padding()
    }.navigationTitle(contact).toolbar { Button("Call", systemImage: "phone") { Task { await session.startCall(with: contact) } }.disabled(session.activeCall != nil) } }
}

struct GroupsView: View {
    @Bindable var session: ThriveSession; @State private var creating = false
    var body: some View { List(session.rooms) { room in Button { Task { await session.open(room) } } label: { VStack(alignment: .leading) { Text(room.name); Text(room.role?.capitalized ?? "Public room — tap to join").font(.caption).foregroundStyle(.secondary) } } }.navigationTitle("Groups").refreshable { await session.refreshRooms() }.toolbar { Button("Create Room", systemImage: "plus") { creating = true } }.sheet(isPresented: $creating) { CreateRoomView(session: session) }.navigationDestination(item: $session.openRoom) { _ in RoomView(session: session) }.overlay { if session.rooms.isEmpty { ContentUnavailableView("No Group Rooms", systemImage: "bubble.left.and.bubble.right", description: Text("Pull to refresh or create a room.")) } } }
}

struct CreateRoomView: View {
    @Environment(\.dismiss) private var dismiss; @Bindable var session: ThriveSession
    @State private var name = ""
    @State private var description = ""
    @State private var visibility = "public"
    @State private var expiration = "never"
    var body: some View { NavigationStack { Form { TextField("Room name", text: $name); TextField("Description", text: $description, axis: .vertical); Picker("Visibility", selection: $visibility) { Text("Public").tag("public"); Text("Private").tag("private") }; Picker("Expiration", selection: $expiration) { Text("Never").tag("never"); Text("One day").tag("day"); Text("One week").tag("week"); Text("One month").tag("month"); Text("One year").tag("year"); Text("When everyone leaves").tag("empty") } }.navigationTitle("New Group Room").toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }; ToolbarItem(placement: .confirmationAction) { Button("Create") { Task { await session.createRoom(name: name, description: description, visibility: visibility, expiration: expiration); dismiss() } }.disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) } } } }
}

struct RoomView: View {
    @Bindable var session: ThriveSession
    @State private var draft = ""
    @State private var importing = false
    @State private var selectedMember: RoomMember?
    var body: some View { VStack {
        if let room = session.openRoom, !room.description.isEmpty { Text(room.description).font(.subheadline).foregroundStyle(.secondary).padding(.horizontal) }
        MessageList(messages: session.roomMessages)
        HStack { Button("Attach", systemImage: "paperclip") { importing = true }.accessibilityHint("Choose a file to send to this room"); TextField("Message", text: $draft, axis: .vertical).textFieldStyle(.roundedBorder); Button("Send") { let value = draft; draft = ""; Task { await session.sendRoomMessage(value) } }.disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }.padding()
    }.navigationTitle(session.openRoom?.name ?? "Group").toolbar { ToolbarItemGroup(placement: .primaryAction) { Menu("Members", systemImage: "person.3") { ForEach(session.roomMembers) { member in Button("Message \(member.name)") { selectedMember = member }; if ["owner", "admin"].contains(session.openRoom?.role ?? "") { Menu("Set \(member.name) role") { ForEach(["guest", "user", "moderator", "admin"], id: \.self) { role in Button(role.capitalized) { Task { await session.setRole(role, member: member) } } } } } } }; Button("Join Voice", systemImage: "waveform") { Task { await session.joinGroupCall() } }.disabled(session.activeCall != nil); Menu("Room", systemImage: "ellipsis.circle") { Button("Leave Room", role: .destructive) { Task { await session.leaveRoom() } } } } }.fileImporter(isPresented: $importing, allowedContentTypes: [.data]) { result in if case .success(let url) = result { let access = url.startAccessingSecurityScopedResource(); defer { if access { url.stopAccessingSecurityScopedResource() } }; if let data = try? Data(contentsOf: url) { Task { await session.sendRoomFile(data, filename: url.lastPathComponent) } } } }.navigationDestination(item: $selectedMember) { member in DirectChatView(session: session, contact: member.name) } }
}

struct MessageList: View {
    let messages: [ChatMessage]
    var body: some View { ScrollViewReader { proxy in ScrollView { LazyVStack(alignment: .leading, spacing: 12) { ForEach(messages) { message in VStack(alignment: .leading, spacing: 2) { Text(message.sender).font(.caption).bold(); Text(message.kind == "file" ? "File: \(message.filename)" : message.body); Text(message.sentAt, style: .time).font(.caption2).foregroundStyle(.secondary) }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal).id(message.id).accessibilityElement(children: .combine) } }.padding(.vertical) }.onChange(of: messages.count) { _, _ in if let id = messages.last?.id { withAnimation { proxy.scrollTo(id, anchor: .bottom) } } } } }
}

struct SettingsView: View {
    @Bindable var session: ThriveSession
    var body: some View { Form { Section("Connected Server") { LabeledContent("Host", value: session.host); LabeledContent("Port", value: String(session.port)) }; Section { Button("Sign Out", role: .destructive) { session.disconnect() } }; Section("About") { LabeledContent("Version", value: "15.10.0"); Link("Thrive Messenger website", destination: URL(string: "https://im.tappedin.fm/thrive-messenger/")!); Link("Privacy Policy", destination: URL(string: "https://im.tappedin.fm/thrive-messenger/privacy/")!); Link("Support", destination: URL(string: "https://im.tappedin.fm/thrive-messenger/support/")!); Link("Original project by G4p Studios", destination: URL(string: "https://galaxy4productions.com")!); Link("Open-source project", destination: URL(string: "https://github.com/G4p-Studios/ThriveMessenger")!) }; Section { Text("This TappedIn distribution is based on the original open-source Thrive Messenger project and includes additional server, group, voice, administration, update, and platform work.").font(.footnote).foregroundStyle(.secondary) } }.navigationTitle("Settings") }
}

struct ActiveCallView: View {
    @Bindable var session: ThriveSession
    var body: some View { NavigationStack { VStack(spacing: 28) { Image(systemName: "waveform.circle.fill").font(.system(size: 84)).foregroundStyle(.tint); Text(session.activeCall?.title ?? "Voice Call").font(.title); Toggle("Mute microphone", isOn: $session.isMuted); Toggle("Deafen speaker", isOn: $session.isDeafened); Button("End Call", role: .destructive) { Task { await session.endCall() } }.buttonStyle(.borderedProminent) }.padding().navigationTitle("Voice Call") } }
}
