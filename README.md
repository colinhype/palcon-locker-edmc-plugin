# PalCon Locker EDMC Plugin

Uploads Elite Dangerous journal events from EDMarketConnector to PalCon Locker.

Download the latest release from:
https://github.com/colinhype/palcon-locker-edmc-plugin/releases/latest

## Install

1. Download the latest release zip.
2. Open EDMarketConnector.
3. Go to File > Settings > Plugins.
4. Open the plugins folder.
5. Extract the `PalConLocker` folder into the plugins folder.
6. Restart EDMarketConnector.

## Updating

Download the latest release and replace the existing `PalConLocker` folder.

## Current version
###v0.8.3
- Added plugin version reporting to PalCon Locker uploads.
- Added server-side tracking of commander plugin versions.
- Added update notification framework for future releases.
- Improved mission tracking and mission completion attribution.
- Corrected combat bond messaging to show bonds as earned rather than handed in.
- Added plugin installation folder validation and warning message.
- Improved upload status messages and activity feedback.
- Various reliability improvements, bug fixes and logging enhancements.

## Changelog
### v0.8.2
Fixed a startup issue that could cause the plugin to stop processing journal events for some commanders. Improves reliability of sector refreshes, mission tracking, and activity uploads. Recommended update for all users.

### v0.8.1
- Initial public release
- Commander activity uploads
- Mission tracking
- Trade tracking
- Exploration tracking
- Powerplay tracking
- Sector watchlists
