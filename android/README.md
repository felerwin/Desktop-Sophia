# Ember Android preview

Open this directory in Android Studio, allow Gradle sync to finish, then run the
`app` configuration on an Android 8.0+ device. The project targets Android 16.

On first launch, enter the Windows desktop's private IPv4 address, port `8766`,
and the value from `mobile_access_token` in Ember's `config.json`. Long-press the
app at any time to reopen connection settings.

This v0.1 client provides Ember's animated body, live phase/body synchronization,
shared message history, and text turns routed through the existing desktop brain.
The desktop must be awake and running Ember. Voice capture, camera input, Android
notifications, and streamed Kokoro audio are intentionally left for later protocol
versions rather than being mocked with separate phone-only behavior.
