# Thrive Messenger App Store metadata

## App information

- Name: Thrive Messenger
- Subtitle: Accessible Community Chat
- Primary category: Social Networking
- Secondary category: Productivity
- Price: Free
- Content rights: Yes. The app displays and transmits user-supplied messages, files, links, room information, and server-supplied content. Users and server operators must have the rights needed to share that content.

## Version information

Promotional text:

> Accessible direct messaging, group rooms, room roles, file sharing, and voice chat for hosted and self-hosted Thrive servers.

Description:

> Thrive Messenger brings approachable, keyboard- and screen-reader-friendly community messaging to iPhone and iPad.
>
> Use Thrive Messenger to:
> - Send direct messages to people on your server
> - Create, join, and participate in group rooms
> - Use owner, administrator, moderator, user, and guest room roles
> - Share files when the room and server allow it
> - Make direct voice calls and join group voice conversations
> - Connect to the TappedIn community server or another compatible self-hosted server
>
> Server operators control accounts, moderation, room permissions, retention, file limits, and available modules. Features and policies can therefore vary by server.
>
> This TappedIn distribution is based on the original open-source Thrive Messenger project by G4p Studios and includes additional server, group, voice, administration, update, and Apple-platform work.

Keywords:

`chat,messaging,groups,voice,community,accessible,rooms,self-hosted,moderation,server`

URLs:

- Support: https://im.tappedin.fm/thrive-messenger/support/
- Marketing: https://im.tappedin.fm/thrive-messenger/
- Privacy policy: https://im.tappedin.fm/thrive-messenger/privacy/
- Privacy choices: https://im.tappedin.fm/thrive-messenger/privacy-choices/
- Terms: https://im.tappedin.fm/thrive-messenger/terms/

## Review notes

Thrive Messenger is a client for compatible Thrive Messenger servers. The default server is `im.tappedin.fm` on TLS port 2005. Accounts, room permissions, moderation, retention, file limits, and optional features are controlled by the connected server.

This build is free and has no advertising, in-app purchases, subscriptions, or external purchase links. It does not use third-party analytics or advertising SDKs. Voice audio is sent only while a user is in an active direct or group call. The default TappedIn server relays call audio to current participants and does not intentionally retain call audio. Self-hosted server operators publish and control their own policies.

Provide a dedicated App Review username and password before submission. The account must be able to sign in, view contacts, join a test room, send a direct and room message, and enter a voice room. Do not place review credentials in public source control.

Original project: https://github.com/G4p-Studios/ThriveMessenger
Original developer site: https://galaxy4productions.com

## App privacy questionnaire baseline

Answer **Yes** to data collection for the default TappedIn service. Declare only data actually processed by the app/service:

- Contact info: email address, if supplied during account creation or recovery; linked to identity; app functionality and account management.
- User content: messages and files; linked to identity; app functionality and moderation/safety.
- Identifiers: username/account identifier; linked to identity; app functionality and account management.
- Usage data: room membership, presence, and server activity needed to operate and protect the service; linked to identity; app functionality, security, and moderation.
- Diagnostics: server-side connection/error records when retained; potentially linked to identity; app functionality and security.
- Audio data: voice audio is processed transiently to provide a live call and is not intentionally retained by the default service. Confirm Apple questionnaire treatment at submission time.

For all categories: not used for third-party advertising, developer advertising, or tracking. No data broker sale. Independent self-hosted servers are separate operators with their own policies.

## Age-rating and compliance baseline

- User-generated content and messaging: present.
- Parental controls: not built into this client.
- Age assurance: not built into this client.
- Advertising: none.
- Gambling, loot boxes, contests, alcohol, tobacco, drugs, medical content, horror, violence, sexual content, or profanity supplied by the developer: none.
- Users could transmit mature or objectionable content: yes; server moderation, blocking, room roles, and server policies apply.
- Unrestricted web access: no embedded general-purpose web browser.
- Encryption/export compliance: the app uses Apple Network framework TLS and Apple system cryptography for transport; no proprietary encryption implementation is shipped. Complete Apple’s exemption questions consistently and retain the annual self-classification record if Apple requires one.

## Submission gates

- Dedicated private App Review account and stable review room.
- Physical-device direct/group voice test between iOS and desktop.
- VoiceOver focus, labels, dynamic type, contrast, and keyboard test.
- Screenshots captured from the final UI without private account data.
- App Store Connect build reaches `VALID`.
- Privacy questionnaire reviewed against the live default-server retention configuration.
- EU DSA trader status verified by the Account Holder/Admin using real display contact information and supporting documentation.
