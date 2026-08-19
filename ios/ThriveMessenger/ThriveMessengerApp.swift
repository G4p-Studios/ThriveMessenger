import SwiftUI

@main
struct ThriveMessengerApp: App {
    @State private var session = ThriveSession()

    var body: some Scene {
        WindowGroup {
            RootView(session: session)
        }
    }
}

struct RootView: View {
    @Bindable var session: ThriveSession

    var body: some View {
        if session.isSignedIn {
            TabView {
                NavigationStack { ContactsView(session: session) }
                    .tabItem { Label("Contacts", systemImage: "person.2") }
                NavigationStack { GroupsView(session: session) }
                    .tabItem { Label("Groups", systemImage: "bubble.left.and.bubble.right") }
                NavigationStack { SettingsView(session: session) }
                    .tabItem { Label("Settings", systemImage: "gear") }
            }
            .sheet(item: $session.activeCall) { _ in ActiveCallView(session: session).interactiveDismissDisabled() }
            .alert(item: $session.incomingCall) { call in
                Alert(title: Text("Incoming Voice Call"), message: Text("\(call.caller) is calling."), primaryButton: .default(Text("Answer")) { Task { await session.answerCall() } }, secondaryButton: .cancel(Text("Decline")) { Task { await session.declineCall() } })
            }
        } else {
            LoginView(session: session)
        }
    }
}
