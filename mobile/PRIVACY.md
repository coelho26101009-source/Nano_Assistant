# NANO for Android — Privacy policy

_Last updated: 20 November 2026_

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
your agenda and memory. They go to your PC encrypted with AES-256-GCM and a key
only your phone and your PC know: directly over your local network or your own
private network (such as Tailscale), or, away from home, through NANO's relay
(a Cloudflare Worker run for NANO). The relay only passes the encrypted messages
between your phone and your PC: it cannot read or change them, keeps nothing,
and sees only your IP address, the sizes of the messages and when they were
sent. Your PC can turn this off (Settings → Phone → Away from home). Which model the NANO on your PC uses to
answer (a local model or an AI service you configured) is decided on the PC; see
the PC app's [privacy notes](../PRIVACY.md).

## Camera

The app uses the camera only when you ask it to: when you tap the camera button
in the chat, or when NANO on your PC asks to see something and you tap **Open the
camera** on its request card (you can always tap **Decline**; NANO never opens
the camera by itself, and there is no "always allow"). While the camera is open a
red **Camera on** sign is shown. One still photo is taken when you press the
shutter: no video, no sound. It is not saved on the phone or in your gallery. It
goes to your PC encrypted like your messages, where a vision model describes it
(a local one first when your PC has one; otherwise the AI service chosen on your
PC, as the request card says) and the photo is then dropped. Only the written
description is kept, in that conversation.

## Voice

When you tap the microphone, Android's speech recognition turns your voice into
text. Depending on your phone this happens on the device or through Google's
speech service, under Google's terms. The app does not record or keep audio.
Reading answers aloud uses Android's text-to-speech engine.

## Updates (test version only)

The test version you install from GitHub looks for a newer test build at most
twice a day, by downloading a small signed file from github.com (Settings →
About → Check for updates automatically turns this off). GitHub sees your IP
address, as with any download; nothing about you or your conversations is sent.
Installing always asks you first. The Play Store version has no updater:
Google Play updates it.

## Permissions

- **Network**: to talk to your PC.
- **Microphone**: only while the microphone button is on.
- **Camera**: only while the camera screen is open, after you tapped to open it.
- **Notifications, exact alarms, start at boot**: so reminders ring on time, also after a restart.
- **Install apps** (test version only): to open Android's installer for a new test build. Android asks you every time.

The QR scanner is Google's code scanner: it returns only the code it read and does
not use the app's camera access.

## Deleting your data

Settings → Forget this PC deletes the key on the phone and the PC stops accepting
it. Uninstalling the app deletes everything it stored. Conversations and memories
live on your PC and are deleted there (Settings → Privacy → Erase all data).

## Children

The app is not directed at children under 13.

## Contact

Questions or requests: open an issue at
https://github.com/coelho97sc/Nano_Assistant/issues
