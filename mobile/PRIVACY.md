# NANO for Android — Privacy policy

_Last updated: 19 November 2026_

NANO for Android is a remote for the NANO assistant that runs on **your own PC**.
The developer runs no servers for it, and the app has no accounts, no ads and no
usage analytics. The same text is shown in the app (Settings → Privacy policy).

## What stays on your phone

- The pairing key for your PC, kept in the Android Keystore.
- Your choices: language, theme and voice.
- The reminders the app schedules as notifications.

Android backup is turned off for this app, so none of this leaves your phone.

## What goes to your PC

The messages you type or say, your answers to approval requests, and changes to
your agenda and memory. They go directly to your PC, over your local network or
your own private network (such as Tailscale), encrypted with AES-256-GCM and a
key only your phone and your PC know. Which model the NANO on your PC uses to
answer (a local model or an AI service you configured) is decided on the PC; see
the PC app's [privacy notes](../PRIVACY.md).

## Voice

When you tap the microphone, Android's speech recognition turns your voice into
text. Depending on your phone this happens on the device or through Google's
speech service, under Google's terms. The app does not record or keep audio.
Reading answers aloud uses Android's text-to-speech engine.

## Permissions

- **Network**: to talk to your PC.
- **Microphone**: only while the microphone button is on.
- **Notifications, exact alarms, start at boot**: so reminders ring on time, also after a restart.

The QR scanner is Google's code scanner: it returns only the code it read, so the
app does not ask for camera access.

## Deleting your data

Settings → Forget this PC deletes the key on the phone and the PC stops accepting
it. Uninstalling the app deletes everything it stored. Conversations and memories
live on your PC and are deleted there (Settings → Privacy → Erase all data).

## Children

The app is not directed at children under 13.

## Contact

Questions or requests: open an issue at
https://github.com/coelho26101009-source/Nano_Assistant/issues
