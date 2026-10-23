# Backlog

Open items only; a fixed one is deleted. Found on the MCB capture (`ugv/dss/mcb-pcb` `8ddb931`, 2026-10-23), each
worked around by a script in that session, none fixed here yet.

- **`edit_schematic` `relink` keeps the old library reference and database table name** (`server/AltiumScript/schematic_edit.pas:575-587`): a part relinked to another library still names its old `SourceLibraryName` and `DatabaseTableName`. Fix: set both from the target; until then a follow-up script corrects them.
- **`delete_component` and `detach_pin` leave net labels floating** although the tool reports them removed; the labels have to be deleted by ID afterwards. Fix: remove the labels the deleted wire stubs carried, and report what was actually removed.
- **`no_erc_pins` adds a second No-ERC marker where one already sits.** Fix: check for an existing marker on the pin before placing.
- **`replace` can leave pins unconnected and report failure** (J3's USB swap: three pins left open). Fix: after the swap, re-wire by pin name where the new symbol's pins match, and list the pins it could not rewire.
