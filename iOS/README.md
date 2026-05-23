# Markd — iOS

Native iOS client for Markd. Talks to the API in `../Backend/`.

Not yet initialized. To start the project:

1. Open Xcode → File → New → Project → iOS App (SwiftUI, Swift).
2. Set the project location to this directory (`iOS/`) with product name `Markd`.
3. Use the bundle id `cc.appfoundry.markd` (or whatever matches your Android package).
4. Commit `Markd/`, `Markd.xcodeproj/`, `MarkdTests/`, `MarkdUITests/`. The root `.gitignore` already excludes Xcode build output and `xcuserdata/`.

See `../Web/` for the existing web app and `../Backend/` for the shared API.
