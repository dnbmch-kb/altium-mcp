// schematic_edit.pas - edit an EXISTING schematic sheet from a spec file (edit_schematic, replace_component),
// annotate sheets (annotate_schematic) and open a project (open_project).
//
// The spec is written by server/schematic_edit.py: one pipe-delimited op per line, coordinates in mils,
// components referenced as "uid:<UNIQUEID>" or "des:<designator>". Every op that cannot be carried out is
// reported in "errors" and changes nothing; the rest of the spec still runs. A pin's new connection (PIN, MAP,
// ATTACH) is not drawn here: it is reported in "pending", GEOMETRY reports the sheet's parts and connection
// objects, server/sheet_plan.py plans where each connection goes, and a second spec draws the plan with WIRE,
// JUNCTION, LABEL and PORT lines.

const
    ED_STUB = 100;          // mils
    ED_EDIT_SPEC = 'edit_spec.txt';             // in the request's folder (ROOT_DIR)
    ED_ANNOTATE_SPEC = 'annotate_spec.txt';

var
    EdDoc       : ISch_Document;
    EdSheetPath : String;
    EdErrors    : TStringList;
    EdPlaced    : TStringList;
    EdPower     : TStringList;    // "NET=style,orient,shown"
    EdPending   : TStringList;    // [part key, "pin", "net", x, y, direction]
    EdGeom      : String;
    EdOps       : Integer;

procedure EdError(Line: String; Why: String);
begin
    EdErrors.Add('"' + JSONEscapeString(Why + ' -- ' + Line) + '"');
end;

function EdBool(B: Boolean): String;
begin
    if B then Result := 'true' else Result := 'false';
end;

function EdJoin(L: TStringList): String;
var
    k : Integer;
begin
    Result := '';
    for k := 0 to L.Count - 1 do
    begin
        if (k > 0) then Result := Result + ', ';
        Result := Result + L[k];
    end;
end;

procedure EdRegister(Obj: ISch_GraphicalObject);
begin
    EdDoc.RegisterSchObjectInContainer(Obj);
    SchServer.RobotManager.SendMessage(EdDoc.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
end;

procedure EdRemove(Obj: ISch_GraphicalObject);
begin
    EdDoc.RemoveSchObject(Obj);
    SchServer.RobotManager.SendMessage(EdDoc.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
end;

procedure EdBeginModify(Obj: ISch_GraphicalObject);
begin
    SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
end;

procedure EdEndModify(Obj: ISch_GraphicalObject);
begin
    SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
end;

// Objects the sheet itself owns (the document iterator also returns component children)
function EdOwnedBySheet(Obj: ISch_GraphicalObject): Boolean;
begin
    Result := False;
    if (Obj.Container <> nil) then
        Result := (Obj.Container.ObjectId = 32);   // the sheet; DelphiScript declares no eSchDocument
end;

function EdFindUid(Uid: String): ISch_GraphicalObject;
var
    Iter : ISch_Iterator;
    Obj  : ISch_GraphicalObject;
begin
    Result := nil;
    Iter := EdDoc.SchIterator_Create;
    Obj := Iter.FirstSchObject;
    while (Obj <> nil) and (Result = nil) do
    begin
        if (Obj.UniqueId = Uid) and EdOwnedBySheet(Obj) then Result := Obj;
        Obj := Iter.NextSchObject;
    end;
    EdDoc.SchIterator_Destroy(Iter);
end;

// Interfaces do not survive a TStringList's Objects: collect unique ids, find and remove afterwards
procedure EdRemoveUid(Uid: String);
var
    Obj : ISch_GraphicalObject;
begin
    Obj := EdFindUid(Uid);
    if (Obj <> nil) then EdRemove(Obj);
end;

// "uid:XXXX" or "des:R5"
function EdFindComp(Ref: String): ISch_Component;
var
    Iter : ISch_Iterator;
    Obj  : ISch_Component;
    Key  : String;
begin
    Result := nil;
    Key := Copy(Ref, 5, Length(Ref));
    Iter := EdDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj := Iter.FirstSchObject;
    while (Obj <> nil) and (Result = nil) do
    begin
        if (Copy(Ref, 1, 4) = 'uid:') and (Obj.UniqueId = Key) then Result := Obj;
        if (Copy(Ref, 1, 4) = 'des:') and (Obj.Designator.Text = Key) then Result := Obj;
        Obj := Iter.NextSchObject;
    end;
    EdDoc.SchIterator_Destroy(Iter);
end;

function EdFindPin(Comp: ISch_Component; Des: String): ISch_Pin;
var
    Iter : ISch_Iterator;
    Pin  : ISch_Pin;
begin
    Result := nil;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePin));
    Iter.AddFilter_CurrentPartPrimitives;
    Iter.AddFilter_CurrentDisplayModePrimitives;
    Pin := Iter.FirstSchObject;
    while (Pin <> nil) and (Result = nil) do
    begin
        if (Pin.Designator = Des) then Result := Pin;
        Pin := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
end;

// Hot end in mils; Dir is the pin's outward direction (0 right, 1 up, 2 left, 3 down)
procedure EdPinEnd(Pin: ISch_Pin; var X: Integer; var Y: Integer; var Dir: Integer);
begin
    GetPinHotEnd(Pin, X, Y);
    Dir := Pin.Orientation;
end;

function EdOnSegment(X, Y, X1, Y1, X2, Y2: Integer): Boolean;
begin
    Result := False;
    if (X1 = X2) and (X = X1) then
        Result := ((Y >= Y1) and (Y <= Y2)) or ((Y >= Y2) and (Y <= Y1));
    if (Y1 = Y2) and (Y = Y1) then
        Result := Result or (((X >= X1) and (X <= X2)) or ((X >= X2) and (X <= X1)));
end;

function EdWireTouches(W: ISch_Wire; X, Y: Integer): Boolean;
var
    k : Integer;
begin
    Result := False;
    for k := 1 to W.VerticesCount - 1 do
        if EdOnSegment(X, Y, CoordToMils(W.Vertex[k].X), CoordToMils(W.Vertex[k].Y),
                       CoordToMils(W.Vertex[k + 1].X), CoordToMils(W.Vertex[k + 1].Y)) then Result := True;
end;

// Something at (X, Y) other than Skip: a wire through or ending there, a pin hot end, and with Names a port or label
function EdTouchAt(X, Y: Integer; Skip: ISch_GraphicalObject; Names: Boolean): Boolean;
var
    Iter, PinIter : ISch_Iterator;
    Obj, Pin      : ISch_GraphicalObject;
    PX, PY, PD    : Integer;
    SkipAddr      : Integer;
begin
    Result := False;
    // Identity by object address: DelphiScript's <> on two interface references to one
    // object is not reliable, and a part then collides with its own pins.
    SkipAddr := 0;
    if (Skip <> nil) then SkipAddr := Skip.I_ObjectAddress;
    Iter := EdDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eWire, eNetLabel, ePowerObject, ePort, eSchComponent));
    Obj := Iter.FirstSchObject;
    while (Obj <> nil) and (not Result) do
    begin
        if (Obj.I_ObjectAddress <> SkipAddr) and EdOwnedBySheet(Obj) then
        begin
            if (Obj.ObjectId = eWire) then Result := EdWireTouches(Obj, X, Y)
            else if (Obj.ObjectId = eSchComponent) then
            begin
                PinIter := Obj.SchIterator_Create;
                PinIter.AddFilter_ObjectSet(MkSet(ePin));
                PinIter.AddFilter_CurrentPartPrimitives;
                PinIter.AddFilter_CurrentDisplayModePrimitives;
                Pin := PinIter.FirstSchObject;
                while (Pin <> nil) and (not Result) do
                begin
                    EdPinEnd(Pin, PX, PY, PD);
                    if (PX = X) and (PY = Y) then Result := True;
                    Pin := PinIter.NextSchObject;
                end;
                Obj.SchIterator_Destroy(PinIter);
            end
            else if Names and (CoordToMils(Obj.Location.X) = X) and (CoordToMils(Obj.Location.Y) = Y) then Result := True;
        end;
        Obj := Iter.NextSchObject;
    end;
    EdDoc.SchIterator_Destroy(Iter);
end;

function EdSomethingAt(X, Y: Integer; Skip: ISch_GraphicalObject): Boolean;
begin
    Result := EdTouchAt(X, Y, Skip, True);
end;

// A wire or a pin at (X, Y): what keeps a net alive, unlike a bare label or port
function EdLiveAt(X, Y: Integer; Skip: ISch_GraphicalObject): Boolean;
begin
    Result := EdTouchAt(X, Y, Skip, False);
end;

// A power net's port: style, orientation and whether its name shows ("NET=style,orient,shown")
function EdPowerStyle(Net: String; var Style: Integer; var Orient: Integer; var Shown: Boolean): Boolean;
var
    S : String;
begin
    S := EdPower.Values[Net];
    Result := (S <> '');
    if Result then
    begin
        Style := StrToInt(Copy(S, 1, Pos(',', S) - 1));
        S := Copy(S, Pos(',', S) + 1, Length(S));
        Orient := StrToInt(Copy(S, 1, Pos(',', S) - 1));
        Shown := (Copy(S, Pos(',', S) + 1, Length(S)) = '1');
    end;
end;

procedure EdPlaceNetAt(X, Y, Dir: Integer; Net: String);
var
    Obj           : ISch_GraphicalObject;
    Style, Orient : Integer;
    Shown         : Boolean;
begin
    if EdPowerStyle(Net, Style, Orient, Shown) then
    begin
        Obj := SchServer.SchObjectFactory(ePowerObject, eCreate_GlobalCopy);
        Obj.Location := Point(MilsToCoord(X), MilsToCoord(Y));
        Obj.Style := Style;
        Obj.Orientation := Orient;
        Obj.ShowNetName := Shown;
        Obj.Text := Net;
    end
    else
    begin
        Obj := SchServer.SchObjectFactory(eNetLabel, eCreate_GlobalCopy);
        Obj.Location := Point(MilsToCoord(X), MilsToCoord(Y));
        if (Dir = 1) or (Dir = 3) then Obj.Orientation := 1 else Obj.Orientation := 0;
        if (Dir = 2) or (Dir = 3) then Obj.Justification := 2;    // bottom right: the text ends at the point
        Obj.Text := Net;
    end;
    EdRegister(Obj);
end;

// The drawing of a planned connection (server/sheet_plan.py): Just 0 bottom left, 2 bottom right
procedure EdLabel(X, Y, Orient, Just: Integer; Text: String);
var
    Obj : ISch_GraphicalObject;
begin
    Obj := SchServer.SchObjectFactory(eNetLabel, eCreate_GlobalCopy);
    Obj.Location := Point(MilsToCoord(X), MilsToCoord(Y));
    Obj.Orientation := Orient;
    Obj.Justification := Just;
    Obj.Text := Text;
    EdRegister(Obj);
end;

procedure EdPort(X, Y, Orient, Style: Integer; Shown: Boolean; Text: String);
var
    Obj : ISch_GraphicalObject;
begin
    Obj := SchServer.SchObjectFactory(ePowerObject, eCreate_GlobalCopy);
    Obj.Location := Point(MilsToCoord(X), MilsToCoord(Y));
    Obj.Style := Style;
    Obj.Orientation := Orient;
    Obj.ShowNetName := Shown;
    Obj.Text := Text;
    EdRegister(Obj);
end;

procedure EdJunction(X, Y: Integer);
var
    Obj : ISch_GraphicalObject;
begin
    Obj := SchServer.SchObjectFactory(eJunction, eCreate_GlobalCopy);
    Obj.Location := Point(MilsToCoord(X), MilsToCoord(Y));
    EdRegister(Obj);
end;

// A part's key for the plan: designator and location, which EdPut keeps apart. Not I_ObjectAddress, which AD25's
// IntToStr cannot convert (the script paused there, 2026-10-23), nor UniqueId, which every copy of one library
// symbol shares until the next load
function EdKey(Comp: ISch_Component): String;
begin
    Result := '"' + JSONEscapeString(Comp.Designator.Text + '@' + IntToStr(CoordToMils(Comp.Location.X)) + ',' +
              IntToStr(CoordToMils(Comp.Location.Y))) + '"';
end;

// A pin's connection, left for the plan: the part by its key, the pin's electrical end and direction
procedure EdPend(Comp: ISch_Component; PinDes: String; Net: String; X, Y, D: Integer);
begin
    EdPending.Add('[' + EdKey(Comp) + ', "' + JSONEscapeString(PinDes) + '", "' +
                  JSONEscapeString(Net) + '", ' + IntToStr(X) + ', ' + IntToStr(Y) + ', ' + IntToStr(D) + ']');
end;

// What a visible "=Name" text shows: the text of the part's parameter Name
function EdParamText(Comp: ISch_Component; Name: String): String;
var
    Iter  : ISch_Iterator;
    Param : ISch_Parameter;
begin
    Result := '';
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eParameter));
    Param := Iter.FirstSchObject;
    while (Param <> nil) do
    begin
        if (UpperCase(Param.Name) = UpperCase(Name)) then Result := Param.Text;
        Param := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
end;

// A text as [x, y, orientation, justification, "text"]
function EdTextJSON(T: ISch_GraphicalObject; Text: String): String;
begin
    Result := '[' + IntToStr(CoordToMils(T.Location.X)) + ', ' + IntToStr(CoordToMils(T.Location.Y)) + ', ' +
              IntToStr(T.Orientation) + ', ' + IntToStr(T.Justification) + ', "' + JSONEscapeString(Text) + '"]';
end;

// One part for the plan: {key, designator, body [l, b, r, t] of its drawn primitives (pins excluded), pins
// [[pin, x, y, direction, body-end x, body-end y]], texts (designator and visible parameters)}
function EdPartGeometry(Comp: ISch_Component): String;
var
    Iter          : ISch_Iterator;
    Prim          : ISch_GraphicalObject;
    Rect          : IDispatch;
    L, B, R, T    : Integer;
    X, Y, D       : Integer;
    Pins, Texts   : String;
    Body, S       : String;
begin
    L := 999999; B := 999999; R := -999999; T := -999999;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eLine, eRectangle, eArc, ePolyline, eEllipse, ePolygon, eRoundRectangle, eEllipticalArc));
    Iter.AddFilter_CurrentPartPrimitives;
    Iter.AddFilter_CurrentDisplayModePrimitives;
    Prim := Iter.FirstSchObject;
    while (Prim <> nil) do
    begin
        Rect := Prim.BoundingRectangle;
        if (CoordToMils(Rect.Left) < L) then L := CoordToMils(Rect.Left);
        if (CoordToMils(Rect.Bottom) < B) then B := CoordToMils(Rect.Bottom);
        if (CoordToMils(Rect.Right) > R) then R := CoordToMils(Rect.Right);
        if (CoordToMils(Rect.Top) > T) then T := CoordToMils(Rect.Top);
        Prim := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
    Body := '';
    if (L <= R) then Body := IntToStr(L) + ', ' + IntToStr(B) + ', ' + IntToStr(R) + ', ' + IntToStr(T);

    Pins := '';
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePin));
    Iter.AddFilter_CurrentPartPrimitives;
    Iter.AddFilter_CurrentDisplayModePrimitives;
    Prim := Iter.FirstSchObject;
    while (Prim <> nil) do
    begin
        EdPinEnd(Prim, X, Y, D);
        if (Pins <> '') then Pins := Pins + ', ';
        Pins := Pins + '["' + JSONEscapeString(Prim.Designator) + '", ' + IntToStr(X) + ', ' + IntToStr(Y) + ', ' +
                IntToStr(D) + ', ' + IntToStr(CoordToMils(Prim.Location.X)) + ', ' +
                IntToStr(CoordToMils(Prim.Location.Y)) + ']';
        Prim := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);

    Texts := '';
    if not Comp.Designator.IsHidden then Texts := EdTextJSON(Comp.Designator, Comp.Designator.Text);
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eParameter));
    Prim := Iter.FirstSchObject;
    while (Prim <> nil) do
    begin
        if not Prim.IsHidden then
        begin
            S := Prim.Text;
            if (Copy(S, 1, 1) = '=') then S := EdParamText(Comp, Copy(S, 2, Length(S)));
            if (Texts <> '') then Texts := Texts + ', ';
            Texts := Texts + EdTextJSON(Prim, S);
        end;
        Prim := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);

    Result := '{"key": ' + EdKey(Comp) + ', "uid": "' + Comp.UniqueId +
              '", "designator": "' + JSONEscapeString(Comp.Designator.Text) +
              '", "body": [' + Body + '], "pins": [' + Pins + '], "texts": [' + Texts + ']}';
end;

// The sheet for the plan: every part, net label, power port and wire it owns
function EdGeometry(Dummy: Integer): String;
var
    Iter                       : ISch_Iterator;
    Obj                        : ISch_GraphicalObject;
    Parts, Labels, Ports, Wires : TStringList;
    S                          : String;
    k                          : Integer;
begin
    Parts := TStringList.Create;
    Labels := TStringList.Create;
    Ports := TStringList.Create;
    Wires := TStringList.Create;
    Iter := EdDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent, eNetLabel, ePowerObject, eWire));
    Obj := Iter.FirstSchObject;
    while (Obj <> nil) do
    begin
        if EdOwnedBySheet(Obj) then
        begin
            if (Obj.ObjectId = eSchComponent) then Parts.Add(EdPartGeometry(Obj))
            else if (Obj.ObjectId = eNetLabel) then Labels.Add(EdTextJSON(Obj, Obj.Text))
            else if (Obj.ObjectId = ePowerObject) then
                Ports.Add('[' + IntToStr(CoordToMils(Obj.Location.X)) + ', ' + IntToStr(CoordToMils(Obj.Location.Y)) +
                          ', ' + IntToStr(Obj.Orientation) + ', ' + IntToStr(Obj.Style) + ', "' +
                          JSONEscapeString(Obj.Text) + '", ' + EdBool(Obj.ShowNetName) + ']')
            else
            begin
                S := '';
                for k := 1 to Obj.VerticesCount do
                begin
                    if (S <> '') then S := S + ', ';
                    S := S + IntToStr(CoordToMils(Obj.Vertex[k].X)) + ', ' + IntToStr(CoordToMils(Obj.Vertex[k].Y));
                end;
                Wires.Add('[' + S + ']');
            end;
        end;
        Obj := Iter.NextSchObject;
    end;
    EdDoc.SchIterator_Destroy(Iter);
    Result := '{"parts": [' + EdJoin(Parts) + '], "labels": [' + EdJoin(Labels) + '], "ports": [' + EdJoin(Ports) +
              '], "wires": [' + EdJoin(Wires) + ']}';
    Parts.Free;
    Labels.Free;
    Ports.Free;
    Wires.Free;
end;

procedure EdWire(X1, Y1, X2, Y2: Integer);
var
    W : ISch_Wire;
begin
    W := SchServer.SchObjectFactory(eWire, eCreate_GlobalCopy);
    W.Location := Point(MilsToCoord(X1), MilsToCoord(Y1));
    W.InsertVertex := 1;
    W.SetState_Vertex(1, Point(MilsToCoord(X1), MilsToCoord(Y1)));
    W.InsertVertex := 2;
    W.SetState_Vertex(2, Point(MilsToCoord(X2), MilsToCoord(Y2)));
    EdRegister(W);
end;

// Stub out of the pin and name its end
procedure EdStub(X, Y, Dir: Integer; Net: String);
var
    EX, EY : Integer;
begin
    EX := X; EY := Y;
    if (Dir = 0) then EX := X + ED_STUB
    else if (Dir = 1) then EY := Y + ED_STUB
    else if (Dir = 2) then EX := X - ED_STUB
    else EY := Y - ED_STUB;
    EdWire(X, Y, EX, EY);
    EdPlaceNetAt(EX, EY, Dir, Net);
end;

procedure EdNoErc(X, Y: Integer);
var
    Obj : ISch_GraphicalObject;
begin
    Obj := SchServer.SchObjectFactory(eNoERC, eCreate_GlobalCopy);
    Obj.Location := Point(MilsToCoord(X), MilsToCoord(Y));
    EdRegister(Obj);
end;

// The footprint models: every PCBLIB model replaced by Footprints, 'F1;F2;...', F1 current and the rest its alternates
// (a DbLib row's Footprint Ref 2 onward). Renaming the first and keeping the others left old models behind, naming
// footprints no library holds (the MCB's legacy resistors carried four), which makes Update From Libraries refuse the
// board. Location, the full path of a PcbLib, is for a part without a database row: each model takes its footprint
// from that library by path, not from the component library, which has no row to give it (the ECO reported Footprint
// Not Found, dss-r0-aiv CTRL, 2026-10-24).
procedure EdSetModel(Comp: ISch_Component; Footprints: String; Location: String);
var
    Iter      : ISch_Iterator;
    Impl, Old : ISch_Implementation;
    Rest      : String;
    Name      : String;
    P         : Integer;
    First     : Boolean;
begin
    if (Footprints = '') then Exit;
    repeat
        Old := nil;
        Iter := Comp.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := Iter.FirstSchObject;
        while (Impl <> nil) do
        begin
            if (Old = nil) and (UpperCase(Impl.ModelType) = 'PCBLIB') then Old := Impl;
            Impl := Iter.NextSchObject;
        end;
        Comp.SchIterator_Destroy(Iter);
        if (Old <> nil) then Comp.RemoveSchImplementation(Old);
    until (Old = nil);
    Rest := Footprints;
    First := True;
    while (Rest <> '') do
    begin
        P := Pos(';', Rest);
        if (P = 0) then
        begin
            Name := Rest;
            Rest := '';
        end
        else
        begin
            Name := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        end;
        Impl := Comp.AddSchImplementation;
        Impl.ModelName := Name;
        Impl.ModelType := 'PCBLIB';
        Impl.AddDataFileLink(Name, Location, 'PCBLib');
        Impl.IsCurrent := First;
        Impl.UseComponentLibrary := (Location = '');
        First := False;
    end;
end;

// Every model of a part linked to an IntLib or DbLib taken from that library ("Use Component Library"), as a
// hand-placed part's are. A copy out of a SchLib keeps the symbol's models on "any library", and the ECO then
// searches every installed library for each one: the raw-head adapter's ECO ran 40 minutes and froze Altium.
// Footprints given a library path (Location) keep it.
procedure EdBindModels(Comp: ISch_Component; Location: String);
var
    Iter : ISch_Iterator;
    Impl : ISch_Implementation;
    Lib  : String;
begin
    Lib := UpperCase(Comp.SourceLibraryName);
    if (Copy(Lib, Length(Lib) - 6, 7) <> '.INTLIB') and (Copy(Lib, Length(Lib) - 5, 6) <> '.DBLIB') then Exit;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eImplementation));
    Impl := Iter.FirstSchObject;
    while (Impl <> nil) do
    begin
        if (Location = '') or (UpperCase(Impl.ModelType) <> 'PCBLIB') then Impl.UseComponentLibrary := True;
        Impl := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
end;

// The part's models as read back: [{type, name, current, use_component_library, library_path}]; library_path is
// the first data file link's location, empty on "any library" or the component library
function EdModels(Comp: ISch_Component): String;
var
    Iter : ISch_Iterator;
    Impl : ISch_Implementation;
    Path : String;
begin
    Result := '';
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eImplementation));
    Impl := Iter.FirstSchObject;
    while (Impl <> nil) do
    begin
        Path := '';
        if (Impl.DatafileLinkCount > 0) then Path := Impl.DatafileLink[0].Location;
        if (Result <> '') then Result := Result + ', ';
        Result := Result + '{"type": "' + JSONEscapeString(Impl.ModelType) + '", "name": "' +
                  JSONEscapeString(Impl.ModelName) + '", "current": ' + EdBool(Impl.IsCurrent) +
                  ', "use_component_library": ' + EdBool(Impl.UseComponentLibrary) +
                  ', "library_path": "' + JSONEscapeString(Path) + '"}';
        Impl := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
end;

// LINK fields: link_library|db_table|design_item|footprints|comment starting at field F; the footprints' library
// path in field L, empty in a spec without it. A library given names the whole link: its table replaces the old one,
// or clears it for an IntLib, so a part relinked to another library keeps no old table.
procedure EdSetLink(Comp: ISch_Component; Rec: String; F: Integer; L: Integer);
var
    Table : String;
begin
    Table := GetFieldFromPipeString(Rec, F + 1);
    if (GetFieldFromPipeString(Rec, F) <> '') then
    begin
        Comp.SourceLibraryName := GetFieldFromPipeString(Rec, F);
        Comp.DatabaseTableName := Table;
        Comp.UseDBTableName := (Table <> '');
    end
    else if (Table <> '') then
    begin
        Comp.DatabaseTableName := Table;
        Comp.UseDBTableName := True;
    end;
    if (GetFieldFromPipeString(Rec, F + 2) <> '') then Comp.DesignItemId := GetFieldFromPipeString(Rec, F + 2);
    EdSetModel(Comp, GetFieldFromPipeString(Rec, F + 3), GetFieldFromPipeString(Rec, L));
    if (GetFieldFromPipeString(Rec, F + 4) <> '') then Comp.Comment.Text := GetFieldFromPipeString(Rec, F + 4);
    EdBindModels(Comp, GetFieldFromPipeString(Rec, L));
end;

procedure EdSetParam(Comp: ISch_Component; PName: String; PVal: String);
var
    Iter   : ISch_Iterator;
    Param  : ISch_Parameter;
    Exists : Boolean;
begin
    Exists := False;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eParameter));
    Param := Iter.FirstSchObject;
    while (Param <> nil) do
    begin
        if (UpperCase(Param.Name) = UpperCase(PName)) then
        begin
            Param.Text := PVal;
            Exists := True;
        end;
        Param := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
    if not Exists then
    begin
        Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
        Param.Name := PName;
        Param.Text := PVal;
        Param.IsHidden := True;
        Comp.AddSchObject(Param);
        SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Param.I_ObjectAddress);
    end;
end;

// Copy a symbol out of a .SchLib onto EdDoc. Opening the library makes it current: EdDoc is held by reference.
function EdReplicate(LibPath: String; LibRef: String): ISch_Component;
var
    LibDoc : ISch_Document;
    Iter   : ISch_Iterator;
    Prim   : ISch_Component;
    Found  : ISch_Component;
begin
    Result := nil;
    OpenLibraryDocument('SchLib', LibPath);
    Sleep(500);
    LibDoc := SchServer.GetCurrentSchDocument;
    if (LibDoc = nil) then Exit;
    if (LibDoc.ObjectID <> 33) then Exit;
    Found := nil;
    Iter := LibDoc.SchLibIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    Prim := Iter.FirstSchObject;
    while (Prim <> nil) do
    begin
        if (Prim.LibReference = LibRef) then Found := Prim;
        Prim := Iter.NextSchObject;
    end;
    LibDoc.SchIterator_Destroy(Iter);
    if (Found <> nil) then Result := ReplicateLibSymbol(Found);
end;

// True when no pin end or stub end of Comp lands on anything already on the sheet
function EdClearSpot(Comp: ISch_Component): Boolean;
var
    Iter         : ISch_Iterator;
    Pin          : ISch_Pin;
    X, Y, D      : Integer;
begin
    Result := True;
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePin));
    Iter.AddFilter_CurrentPartPrimitives;
    Iter.AddFilter_CurrentDisplayModePrimitives;
    Pin := Iter.FirstSchObject;
    while (Pin <> nil) and Result do
    begin
        EdPinEnd(Pin, X, Y, D);
        if EdSomethingAt(X, Y, Comp) then Result := False;
        if (D = 0) then X := X + ED_STUB else if (D = 1) then Y := Y + ED_STUB
        else if (D = 2) then X := X - ED_STUB else Y := Y - ED_STUB;
        if EdSomethingAt(X, Y, Comp) then Result := False;
        Pin := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
end;

// Register Comp at (X, Y); step it sideways in 500 mil steps until its pins and stubs touch nothing
function EdPut(Comp: ISch_Component; X, Y, Orient, Mirror: Integer): Boolean;
var
    Step : Integer;
begin
    Comp.Orientation := Orient;
    EdDoc.RegisterSchObjectInContainer(Comp);
    SchServer.RobotManager.SendMessage(EdDoc.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Comp.I_ObjectAddress);
    if (Mirror = 1) then Comp.Mirror(Comp.Location);
    Comp.MoveByXY(MilsToCoord(X - CoordToMils(Comp.Location.X)), MilsToCoord(Y - CoordToMils(Comp.Location.Y)));
    Step := 0;
    while (not EdClearSpot(Comp)) and (Step < 20) do
    begin
        Comp.MoveByXY(MilsToCoord(500), 0);
        Step := Step + 1;
    end;
    Result := (Step < 20);
end;

procedure EdReportPlaced(Comp: ISch_Component; Unassigned: String);
var
    Iter    : ISch_Iterator;
    Pin     : ISch_Pin;
    X, Y, D : Integer;
    Pins    : String;
begin
    Pins := '';
    Iter := Comp.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(ePin));
    Iter.AddFilter_CurrentPartPrimitives;
    Iter.AddFilter_CurrentDisplayModePrimitives;
    Pin := Iter.FirstSchObject;
    while (Pin <> nil) do
    begin
        EdPinEnd(Pin, X, Y, D);
        if (Pins <> '') then Pins := Pins + ', ';
        Pins := Pins + '["' + JSONEscapeString(Pin.Designator) + '", ' + IntToStr(X) + ', ' + IntToStr(Y) + ']';
        Pin := Iter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(Iter);
    EdPlaced.Add('{"designator": "' + JSONEscapeString(Comp.Designator.Text) + '", "uid": "' + Comp.UniqueId +
                 '", "x": ' + IntToStr(CoordToMils(Comp.Location.X)) + ', "y": ' + IntToStr(CoordToMils(Comp.Location.Y)) +
                 ', "pins": [' + Pins + '], "unassigned_pins": [' + Unassigned + '], "library": "' +
                 JSONEscapeString(Comp.SourceLibraryName) + '", "design_item": "' + JSONEscapeString(Comp.DesignItemId) +
                 '", "models": [' + EdModels(Comp) + ']}');
end;

// Finish a PLACE / REPLACE / RELINK block: DESCRIPTION, PARAM, PIN and NOPIN lines up to END. DESCRIPTION is the
// part's own Description, as build_schematic sets it: Altium keeps no parameter of that name.
function EdFinishPart(Comp: ISch_Component; Spec: TStringList; var i: Integer; Line: String): Boolean;
var
    Rec, Kind, Unassigned, Have : String;
    Pin                         : ISch_Pin;
    Iter                        : ISch_Iterator;
    X, Y, D                     : Integer;
begin
    Result := True;
    Have := '|';
    i := i + 1;
    while (i < Spec.Count) and (GetFieldFromPipeString(Spec[i], 0) <> 'END') do
    begin
        Rec := Spec[i];
        Kind := GetFieldFromPipeString(Rec, 0);
        if (Kind = 'PARAM') then EdSetParam(Comp, GetFieldFromPipeString(Rec, 1), GetFieldFromPipeString(Rec, 2))
        else if (Kind = 'DESCRIPTION') then Comp.ComponentDescription := GetFieldFromPipeString(Rec, 1)
        else if (Kind = 'MAP') then
        begin
            // MAP|old|new|net: the old end was labelled before the swap; stub the new pin with the same net
            Pin := EdFindPin(Comp, GetFieldFromPipeString(Rec, 2));
            if (Pin = nil) then
            begin
                EdError(Rec, 'no such pin on the new part');
                Result := False;
            end
            else
            begin
                EdPinEnd(Pin, X, Y, D);
                EdPend(Comp, GetFieldFromPipeString(Rec, 2), GetFieldFromPipeString(Rec, 3), X, Y, D);
                Have := Have + GetFieldFromPipeString(Rec, 2) + '|';
            end;
        end
        else if (Kind = 'PIN') or (Kind = 'NOPIN') then
        begin
            Pin := EdFindPin(Comp, GetFieldFromPipeString(Rec, 1));
            if (Pin = nil) then
            begin
                EdError(Rec, 'no such pin on ' + Comp.Designator.Text);
                Result := False;
            end
            else
            begin
                EdPinEnd(Pin, X, Y, D);
                if (Kind = 'PIN') then EdPend(Comp, GetFieldFromPipeString(Rec, 1), GetFieldFromPipeString(Rec, 2), X, Y, D)
                else EdNoErc(X, Y);
                Have := Have + GetFieldFromPipeString(Rec, 1) + '|';
            end;
        end;
        i := i + 1;
    end;
    // pins the spec left unconnected, by name, so a readback can tell intent from omission
    Unassigned := '';
    if (GetFieldFromPipeString(Line, 0) <> 'RELINK') then
    begin
        Iter := Comp.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(ePin));
        Iter.AddFilter_CurrentPartPrimitives;
        Iter.AddFilter_CurrentDisplayModePrimitives;
        Pin := Iter.FirstSchObject;
        while (Pin <> nil) do
        begin
            if (Pos('|' + Pin.Designator + '|', Have) = 0) then
            begin
                if (Unassigned <> '') then Unassigned := Unassigned + ', ';
                Unassigned := Unassigned + '"' + JSONEscapeString(Pin.Designator) + '"';
            end;
            Pin := Iter.NextSchObject;
        end;
        Comp.SchIterator_Destroy(Iter);
    end;
    EdReportPlaced(Comp, Unassigned);
end;

// Any component pin end lying on W, at a vertex or mid-segment
function EdPinOnWire(W: ISch_Wire): Boolean;
var
    Iter, PIter : ISch_Iterator;
    Comp        : ISch_Component;
    Pin         : ISch_Pin;
    X, Y, D     : Integer;
begin
    Result := False;
    Iter := EdDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
    Comp := Iter.FirstSchObject;
    while (Comp <> nil) and (not Result) do
    begin
        PIter := Comp.SchIterator_Create;
        PIter.AddFilter_ObjectSet(MkSet(ePin));
        PIter.AddFilter_CurrentPartPrimitives;
        PIter.AddFilter_CurrentDisplayModePrimitives;
        Pin := PIter.FirstSchObject;
        while (Pin <> nil) and (not Result) do
        begin
            EdPinEnd(Pin, X, Y, D);
            if EdWireTouches(W, X, Y) then Result := True;
            Pin := PIter.NextSchObject;
        end;
        Comp.SchIterator_Destroy(PIter);
        Comp := Iter.NextSchObject;
    end;
    EdDoc.SchIterator_Destroy(Iter);
end;

// Delete Comp and, with DeadEnds, every wire left connecting nothing, then the labels and ports on them
procedure EdDeleteComponent(Comp: ISch_Component; DeadEnds: Boolean; Keep: String);
var
    Points             : TStringList;
    Iter, PIter        : ISch_Iterator;
    Pin, Obj           : ISch_GraphicalObject;
    X, Y, D, k, n      : Integer;
    Changed, Touch     : Boolean;
    Doomed             : TStringList;
    W                  : ISch_Wire;
begin
    Points := TStringList.Create;
    PIter := Comp.SchIterator_Create;
    PIter.AddFilter_ObjectSet(MkSet(ePin));
    PIter.AddFilter_CurrentPartPrimitives;
    PIter.AddFilter_CurrentDisplayModePrimitives;
    Pin := PIter.FirstSchObject;
    while (Pin <> nil) do
    begin
        EdPinEnd(Pin, X, Y, D);
        Points.Add(IntToStr(X) + ',' + IntToStr(Y));
        Pin := PIter.NextSchObject;
    end;
    Comp.SchIterator_Destroy(PIter);
    EdRemove(Comp);
    if not DeadEnds then begin Points.Free; Exit; end;

    Changed := True;
    while Changed do
    begin
        Changed := False;
        Doomed := TStringList.Create;
        Iter := EdDoc.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eWire));
        W := Iter.FirstSchObject;
        while (W <> nil) do
        begin
            if EdOwnedBySheet(W) and (Pos(W.UniqueId, Keep) = 0) then
            begin
                Touch := False;
                for k := 0 to Points.Count - 1 do
                    if EdWireTouches(W, StrToInt(Copy(Points[k], 1, Pos(',', Points[k]) - 1)),
                                        StrToInt(Copy(Points[k], Pos(',', Points[k]) + 1, 20))) then Touch := True;
                // dead when no vertex reaches anything but other doomed points, labels or ports
                if Touch then
                begin
                    n := 0;
                    for k := 1 to W.VerticesCount do
                        if (Points.IndexOf(IntToStr(CoordToMils(W.Vertex[k].X)) + ',' + IntToStr(CoordToMils(W.Vertex[k].Y))) < 0) then
                            if EdLiveAt(CoordToMils(W.Vertex[k].X), CoordToMils(W.Vertex[k].Y), W) then n := n + 1;
                    if (n = 0) and EdPinOnWire(W) then n := 1;      // a pin met mid-segment keeps it alive
                    if (n = 0) then Doomed.Add(W.UniqueId);
                end;
            end;
            W := Iter.NextSchObject;
        end;
        EdDoc.SchIterator_Destroy(Iter);
        for k := 0 to Doomed.Count - 1 do
        begin
            W := EdFindUid(Doomed[k]);
            for n := 1 to W.VerticesCount do
                Points.Add(IntToStr(CoordToMils(W.Vertex[n].X)) + ',' + IntToStr(CoordToMils(W.Vertex[n].Y)));
            EdRemove(W);
            Changed := True;
        end;
        Doomed.Free;
    end;
    // labels and ports left on a doomed point with no wire or pin under them
    Doomed := TStringList.Create;
    Iter := EdDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eNetLabel, ePowerObject));
    Obj := Iter.FirstSchObject;
    while (Obj <> nil) do
    begin
        if EdOwnedBySheet(Obj) and (Pos(Obj.UniqueId, Keep) = 0) then
            if (Points.IndexOf(IntToStr(CoordToMils(Obj.Location.X)) + ',' + IntToStr(CoordToMils(Obj.Location.Y))) >= 0) then
                if not EdLiveAt(CoordToMils(Obj.Location.X), CoordToMils(Obj.Location.Y), Obj) then Doomed.Add(Obj.UniqueId);
        Obj := Iter.NextSchObject;
    end;
    EdDoc.SchIterator_Destroy(Iter);
    for k := 0 to Doomed.Count - 1 do EdRemoveUid(Doomed[k]);
    Doomed.Free;
    Points.Free;
end;

procedure EdSetText(Obj: ISch_GraphicalObject; Text: String);
begin
    EdBeginModify(Obj);
    Obj.Text := Text;
    EdEndModify(Obj);
end;

// Remove what names or feeds a pin's hot end: a port, a label, a NoERC marker there, and a two-point wire whose
// far end carries only a port or label (that port or label goes too). A wire that also serves something else is
// refused, not cut: the op then changes nothing.
procedure EdDetach(Comp: ISch_Component; PinDes: String; Line: String);
var
    Pin                : ISch_Pin;
    X, Y, D, FX, FY, k : Integer;
    Iter, NIter        : ISch_Iterator;
    Obj, Name          : ISch_GraphicalObject;
    Doomed             : TStringList;
    Refused            : String;
begin
    Pin := EdFindPin(Comp, PinDes);
    if (Pin = nil) then
    begin
        EdError(Line, 'no such pin');
        Exit;
    end;
    EdPinEnd(Pin, X, Y, D);
    Refused := '';
    Doomed := TStringList.Create;
    Iter := EdDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eWire, eNetLabel, ePowerObject, eNoERC));
    Obj := Iter.FirstSchObject;
    while (Obj <> nil) do
    begin
        if EdOwnedBySheet(Obj) and (Refused = '') then
        begin
            if (Obj.ObjectId = eWire) then
            begin
                if EdWireTouches(Obj, X, Y) then
                begin
                    if (Obj.VerticesCount <> 2) then Refused := 'the pin sits on a multi-segment wire'
                    else
                    begin
                        if (CoordToMils(Obj.Vertex[1].X) = X) and (CoordToMils(Obj.Vertex[1].Y) = Y) then
                        begin
                            FX := CoordToMils(Obj.Vertex[2].X);
                            FY := CoordToMils(Obj.Vertex[2].Y);
                        end
                        else
                        begin
                            FX := CoordToMils(Obj.Vertex[1].X);
                            FY := CoordToMils(Obj.Vertex[1].Y);
                        end;
                        if EdLiveAt(FX, FY, Obj) then Refused := 'the wire at the pin also serves something else'
                        else
                        begin
                            Doomed.Add(Obj.UniqueId);
                            NIter := EdDoc.SchIterator_Create;
                            NIter.AddFilter_ObjectSet(MkSet(eNetLabel, ePowerObject));
                            Name := NIter.FirstSchObject;
                            while (Name <> nil) do
                            begin
                                if EdOwnedBySheet(Name) and (CoordToMils(Name.Location.X) = FX) and
                                   (CoordToMils(Name.Location.Y) = FY) then Doomed.Add(Name.UniqueId);
                                Name := NIter.NextSchObject;
                            end;
                            EdDoc.SchIterator_Destroy(NIter);
                        end;
                    end;
                end;
            end
            else if (CoordToMils(Obj.Location.X) = X) and (CoordToMils(Obj.Location.Y) = Y) then
                Doomed.Add(Obj.UniqueId);
        end;
        Obj := Iter.NextSchObject;
    end;
    EdDoc.SchIterator_Destroy(Iter);
    if (Refused <> '') then EdError(Line, Refused)
    else if (Doomed.Count = 0) then EdError(Line, 'nothing attached to the pin')
    else
        for k := 0 to Doomed.Count - 1 do EdRemoveUid(Doomed[k]);
    Doomed.Free;
end;

// A sheet symbol's entry: on the symbol's left or right edge, its distance from the symbol's top
procedure EdAttachEntry(SymName, EntryName, Net: String; Line: String);
var
    Iter, EIter : ISch_Iterator;
    Sym         : ISch_SheetSymbol;
    Entry       : ISch_SheetEntry;
    X, Y, Dir   : Integer;
    Done        : Boolean;
begin
    Done := False;
    Iter := EdDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSheetSymbol));
    Sym := Iter.FirstSchObject;
    while (Sym <> nil) and (not Done) do
    begin
        if (Sym.SheetName.Text = SymName) then
        begin
            EIter := Sym.SchIterator_Create;
            EIter.AddFilter_ObjectSet(MkSet(eSheetEntry));
            Entry := EIter.FirstSchObject;
            while (Entry <> nil) and (not Done) do
            begin
                if (Entry.Name = EntryName) then
                begin
                    Y := CoordToMils(Sym.Location.Y) - CoordToMils(Entry.DistanceFromTop);
                    if (Entry.Side = eRightSide) then
                    begin
                        X := CoordToMils(Sym.Location.X) + CoordToMils(Sym.XSize);
                        Dir := 0;
                    end
                    else
                    begin
                        X := CoordToMils(Sym.Location.X);
                        Dir := 2;
                    end;
                    EdStub(X, Y, Dir, Net);
                    Done := True;
                end;
                Entry := EIter.NextSchObject;
            end;
            Sym.SchIterator_Destroy(EIter);
        end;
        Sym := Iter.NextSchObject;
    end;
    EdDoc.SchIterator_Destroy(Iter);
    if not Done then EdError(Line, 'no such sheet symbol or entry');
end;

// Before a part is swapped out: the net its pin carried goes onto the old pin end wherever a wire or pin met it
procedure EdLabelOldEnd(Old: ISch_Component; OldPin: String; Net: String);
var
    Pin     : ISch_Pin;
    X, Y, D : Integer;
begin
    Pin := EdFindPin(Old, OldPin);
    if (Pin = nil) then Exit;
    EdPinEnd(Pin, X, Y, D);
    if EdLiveAt(X, Y, Old) then EdPlaceNetAt(X, Y, D, Net);
end;

procedure EdSkipBlock(Spec: TStringList; var i: Integer);
begin
    while (i < Spec.Count) and (GetFieldFromPipeString(Spec[i], 0) <> 'END') do i := i + 1;
end;

function EditSchematicFromSpec(SpecPath: String): String;
var
    Spec                 : TStringList;
    Rec, Kind, Saved     : String;
    i, j, D, X, Y        : Integer;
    Orient, Mirror       : Integer;
    Obj                  : ISch_GraphicalObject;
    Comp, Old            : ISch_Component;
    SrvDoc               : IServerDocument;
    Iter                 : ISch_Iterator;
begin
    Result := '{"success": false, "error": "edit did not run"}';
    if not FileExists(SpecPath) then
    begin
        Result := '{"success": false, "error": "spec file not found"}';
        Exit;
    end;
    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    EdErrors := TStringList.Create;
    EdPlaced := TStringList.Create;
    EdPower := TStringList.Create;
    EdPending := TStringList.Create;
    EdGeom := 'null';
    EdOps := 0;
    Saved := '';

    EdSheetPath := GetFieldFromPipeString(Spec[0], 1);
    SrvDoc := OpenLibraryDocument('SCH', EdSheetPath);
    EdDoc := SchServer.GetSchDocumentByPath(EdSheetPath);
    if (EdDoc = nil) or (SrvDoc = nil) then
    begin
        Result := '{"success": false, "error": "' + JSONEscapeString('cannot open ' + EdSheetPath) + '"}';
        Exit;
    end;
    if (EdDoc.ObjectID <> 32) then
    begin
        Result := '{"success": false, "error": "target is not a schematic - refusing to edit"}';
        Exit;
    end;

    SchServer.ProcessControl.PreProcess(EdDoc, '');
    i := 1;
    while (i < Spec.Count) do
    begin
        Rec := Spec[i];
        Kind := GetFieldFromPipeString(Rec, 0);
        // one record per op the server can see; a part names its designator
        if (Kind = 'PLACE') or (Kind = 'REPLACE') or (Kind = 'RELINK') then
            BridgeProgress(LowerCase(Kind) + ' ' + GetFieldFromPipeString(Rec, 1), i, Spec.Count - 1)
        else if (Pos('|' + Kind + '|', '|POWERNET|PIN|MAP|MAPOLD|NOPIN|PARAM|END|') = 0) then
            BridgeProgress(LowerCase(Kind), i, Spec.Count - 1);
        if (Pos('|' + Kind + '|', '|POWERNET|SAVE|GEOMETRY|WIRE|JUNCTION|LABEL|PORT|') = 0) then EdOps := EdOps + 1;

        if (Kind = 'POWERNET') then
            EdPower.Add(GetFieldFromPipeString(Rec, 1) + '=' + GetFieldFromPipeString(Rec, 2) + ',' +
                        GetFieldFromPipeString(Rec, 3) + ',' + GetFieldFromPipeString(Rec, 4))
        else if (Kind = 'DELETE') then
        begin
            Obj := EdFindUid(GetFieldFromPipeString(Rec, 1));
            if (Obj = nil) then EdError(Rec, 'no object with that unique id') else EdRemove(Obj);
        end
        else if (Kind = 'DELCOMP') then
        begin
            Comp := EdFindComp('uid:' + GetFieldFromPipeString(Rec, 1));
            if (Comp = nil) then EdError(Rec, 'no component with that unique id')
            else EdDeleteComponent(Comp, GetFieldFromPipeString(Rec, 2) = '1', GetFieldFromPipeString(Rec, 3));
        end
        else if (Kind = 'RENAME_LABEL') or (Kind = 'PORT_RENAME') then
        begin
            Obj := EdFindUid(GetFieldFromPipeString(Rec, 1));
            if (Obj = nil) then EdError(Rec, 'no object with that unique id')
            else EdSetText(Obj, GetFieldFromPipeString(Rec, 2));
        end
        else if (Kind = 'RENAME_NET') then
        begin
            j := 0;
            Iter := EdDoc.SchIterator_Create;
            Iter.AddFilter_ObjectSet(MkSet(eNetLabel));
            Obj := Iter.FirstSchObject;
            while (Obj <> nil) do
            begin
                if EdOwnedBySheet(Obj) and (Obj.Text = GetFieldFromPipeString(Rec, 1)) then
                begin
                    EdSetText(Obj, GetFieldFromPipeString(Rec, 2));
                    j := j + 1;
                end;
                Obj := Iter.NextSchObject;
            end;
            EdDoc.SchIterator_Destroy(Iter);
            if (j = 0) then EdError(Rec, 'no net label with that text');
        end
        else if (Kind = 'LABEL_TO_PORT') then
        begin
            Obj := EdFindUid(GetFieldFromPipeString(Rec, 1));
            if (Obj = nil) then EdError(Rec, 'no object with that unique id')
            else
            begin
                X := CoordToMils(Obj.Location.X);
                Y := CoordToMils(Obj.Location.Y);
                EdRemove(Obj);
                EdPower.Add(GetFieldFromPipeString(Rec, 2) + '=' + GetFieldFromPipeString(Rec, 3) + ',' +
                            GetFieldFromPipeString(Rec, 4) + ',' + GetFieldFromPipeString(Rec, 5));
                EdPlaceNetAt(X, Y, 1, GetFieldFromPipeString(Rec, 2));
            end;
        end
        else if (Kind = 'ATTACH') or (Kind = 'NOERC') then
        begin
            Comp := EdFindComp(GetFieldFromPipeString(Rec, 1));
            Obj := nil;
            if (Comp <> nil) then Obj := EdFindPin(Comp, GetFieldFromPipeString(Rec, 2));
            if (Obj = nil) then EdError(Rec, 'no such component or pin')
            else
            begin
                EdPinEnd(Obj, X, Y, D);
                if (Kind = 'ATTACH') then EdPend(Comp, GetFieldFromPipeString(Rec, 2), GetFieldFromPipeString(Rec, 3), X, Y, D)
                else EdNoErc(X, Y);
            end;
        end
        else if (Kind = 'DETACH') then
        begin
            Comp := EdFindComp(GetFieldFromPipeString(Rec, 1));
            if (Comp = nil) then EdError(Rec, 'no such component')
            else EdDetach(Comp, GetFieldFromPipeString(Rec, 2), Rec);
        end
        else if (Kind = 'ENTRY') then
            EdAttachEntry(GetFieldFromPipeString(Rec, 1), GetFieldFromPipeString(Rec, 2), GetFieldFromPipeString(Rec, 3), Rec)
        else if (Kind = 'PLACE') then
        begin
            Comp := EdReplicate(GetFieldFromPipeString(Rec, 2), GetFieldFromPipeString(Rec, 3));
            if (Comp = nil) then
            begin
                EdError(Rec, 'symbol not found in the library');
                EdSkipBlock(Spec, i);
            end
            else
            begin
                // one part of a multi-part symbol: its pins are the ones EdPut and the PIN lines see
                if (GetFieldFromPipeString(Rec, 13) <> '') then Comp.CurrentPartID := StrToInt(GetFieldFromPipeString(Rec, 13));
                Comp.Designator.Text := GetFieldFromPipeString(Rec, 1);
                if not EdPut(Comp, StrToInt(GetFieldFromPipeString(Rec, 4)), StrToInt(GetFieldFromPipeString(Rec, 5)),
                             StrToInt(GetFieldFromPipeString(Rec, 6)), StrToInt(GetFieldFromPipeString(Rec, 7))) then
                    EdError(Rec, 'no clear spot within 10 inches to the right; placed overlapping');
                EdSetLink(Comp, Rec, 8, 14);
                EdFinishPart(Comp, Spec, i, Rec);
            end;
        end
        else if (Kind = 'REPLACE') then
        begin
            Old := EdFindComp(GetFieldFromPipeString(Rec, 1));
            Comp := nil;
            if (Old <> nil) then Comp := EdReplicate(GetFieldFromPipeString(Rec, 2), GetFieldFromPipeString(Rec, 3));
            if (Comp = nil) then
            begin
                if (Old = nil) then EdError(Rec, 'no such component') else EdError(Rec, 'symbol not found in the library');
                EdSkipBlock(Spec, i);
            end
            else
            begin
                // every MAP line's net goes onto the old pin's end first, so the old wiring keeps its net
                j := i + 1;
                while (j < Spec.Count) and (GetFieldFromPipeString(Spec[j], 0) <> 'END') do
                begin
                    if (GetFieldFromPipeString(Spec[j], 0) = 'MAP') or (GetFieldFromPipeString(Spec[j], 0) = 'MAPOLD') then
                        EdLabelOldEnd(Old, GetFieldFromPipeString(Spec[j], 1), GetFieldFromPipeString(Spec[j], 3));
                    j := j + 1;
                end;
                Comp.Designator.Text := Old.Designator.Text;
                X := CoordToMils(Old.Location.X);
                Y := CoordToMils(Old.Location.Y);
                Orient := StrToInt(GetFieldFromPipeString(Rec, 4));
                if (Orient < 0) then Orient := Old.Orientation;
                Mirror := StrToInt(GetFieldFromPipeString(Rec, 5));
                EdRemove(Old);
                if not EdPut(Comp, X, Y, Orient, Mirror) then
                    EdError(Rec, 'no clear spot within 10 inches to the right; placed overlapping');
                EdSetLink(Comp, Rec, 6, 11);
                EdFinishPart(Comp, Spec, i, Rec);
            end;
        end
        else if (Kind = 'RELINK') then
        begin
            Comp := EdFindComp(GetFieldFromPipeString(Rec, 1));
            if (Comp = nil) then
            begin
                EdError(Rec, 'no such component');
                EdSkipBlock(Spec, i);
            end
            else
            begin
                EdBeginModify(Comp);
                EdSetLink(Comp, Rec, 2, 7);
                EdEndModify(Comp);
                EdFinishPart(Comp, Spec, i, Rec);
            end;
        end
        else if (Kind = 'WIRE') then
            EdWire(StrToInt(GetFieldFromPipeString(Rec, 1)), StrToInt(GetFieldFromPipeString(Rec, 2)),
                   StrToInt(GetFieldFromPipeString(Rec, 3)), StrToInt(GetFieldFromPipeString(Rec, 4)))
        else if (Kind = 'JUNCTION') then
            EdJunction(StrToInt(GetFieldFromPipeString(Rec, 1)), StrToInt(GetFieldFromPipeString(Rec, 2)))
        else if (Kind = 'LABEL') then
            EdLabel(StrToInt(GetFieldFromPipeString(Rec, 1)), StrToInt(GetFieldFromPipeString(Rec, 2)),
                    StrToInt(GetFieldFromPipeString(Rec, 3)), StrToInt(GetFieldFromPipeString(Rec, 4)),
                    GetFieldFromPipeString(Rec, 5))
        else if (Kind = 'PORT') then
            EdPort(StrToInt(GetFieldFromPipeString(Rec, 1)), StrToInt(GetFieldFromPipeString(Rec, 2)),
                   StrToInt(GetFieldFromPipeString(Rec, 3)), StrToInt(GetFieldFromPipeString(Rec, 4)),
                   GetFieldFromPipeString(Rec, 5) = '1', GetFieldFromPipeString(Rec, 6))
        else if (Kind = 'GEOMETRY') then
            EdGeom := EdGeometry(0)
        else if (Kind = 'SAVE') then
        begin
            SchServer.ProcessControl.PostProcess(EdDoc, '');
            EdDoc.GraphicallyInvalidate;
            SrvDoc.Modified := True;
            SrvDoc.DoFileSave('');
            Saved := EdSheetPath;
            SchServer.ProcessControl.PreProcess(EdDoc, '');
        end
        else
            EdError(Rec, 'unknown op');
        i := i + 1;
    end;
    SchServer.ProcessControl.PostProcess(EdDoc, '');
    EdDoc.GraphicallyInvalidate;

    Result := '{"success": ' + EdBool(EdErrors.Count = 0) + ', "sheet": "' + JSONEscapeString(EdSheetPath) +
              '", "saved": "' + JSONEscapeString(Saved) + '", "ops": ' + IntToStr(EdOps) +
              ', "errors": [' + EdJoin(EdErrors) + '], "placed": [' + EdJoin(EdPlaced) + '], "pending": [' +
              EdJoin(EdPending) + '], "geometry": ' + EdGeom + '}';
    Spec.Free;
    EdErrors.Free;
    EdPlaced.Free;
    EdPower.Free;
    EdPending.Free;
end;

// annotate_schematic: SHEET lines, ASSIGN|uid|designator lines resolved over all those sheets, SAVE
function AnnotateFromSpec(SpecPath: String): String;
var
    Spec, Sheets, Done, Missing : TStringList;
    Rec                         : String;
    i, s                        : Integer;
    Doc                         : ISch_Document;
    Iter                        : ISch_Iterator;
    Comp                        : ISch_Component;
    Found                       : Boolean;
    SrvDoc                      : IServerDocument;
begin
    if not FileExists(SpecPath) then
    begin
        Result := '{"success": false, "error": "spec file not found"}';
        Exit;
    end;
    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    Sheets := TStringList.Create;
    Done := TStringList.Create;
    Missing := TStringList.Create;
    for i := 0 to Spec.Count - 1 do
        if (GetFieldFromPipeString(Spec[i], 0) = 'SHEET') then
        begin
            OpenLibraryDocument('SCH', GetFieldFromPipeString(Spec[i], 1));
            Sheets.Add(GetFieldFromPipeString(Spec[i], 1));
        end;
    for i := 0 to Spec.Count - 1 do
    begin
        Rec := Spec[i];
        if (GetFieldFromPipeString(Rec, 0) = 'ASSIGN') then
        begin
            Found := False;
            for s := 0 to Sheets.Count - 1 do
            begin
                Doc := SchServer.GetSchDocumentByPath(Sheets[s]);
                if (Doc <> nil) and (not Found) then
                    if (Doc.ObjectID = 32) then
                    begin
                        Iter := Doc.SchIterator_Create;
                        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                        Comp := Iter.FirstSchObject;
                        while (Comp <> nil) and (not Found) do
                        begin
                            if (Comp.UniqueId = GetFieldFromPipeString(Rec, 1)) then
                            begin
                                Done.Add('{"uid": "' + Comp.UniqueId + '", "old": "' + JSONEscapeString(Comp.Designator.Text) +
                                         '", "new": "' + JSONEscapeString(GetFieldFromPipeString(Rec, 2)) + '"}');
                                SchServer.ProcessControl.PreProcess(Doc, '');
                                SchServer.RobotManager.SendMessage(Comp.Designator.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
                                Comp.Designator.Text := GetFieldFromPipeString(Rec, 2);
                                SchServer.RobotManager.SendMessage(Comp.Designator.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
                                SchServer.ProcessControl.PostProcess(Doc, '');
                                Found := True;
                            end;
                            Comp := Iter.NextSchObject;
                        end;
                        Doc.SchIterator_Destroy(Iter);
                    end;
            end;
            if not Found then Missing.Add('"' + JSONEscapeString(GetFieldFromPipeString(Rec, 1)) + '"');
        end;
    end;
    if (Spec.IndexOf('SAVE') >= 0) then
        for s := 0 to Sheets.Count - 1 do
        begin
            SrvDoc := FindOpenDocument(Sheets[s]);
            if (SrvDoc <> nil) then
            begin
                SrvDoc.Modified := True;
                SrvDoc.DoFileSave('');
            end;
        end;
    Result := '{"success": ' + EdBool(Missing.Count = 0) + ', "assigned": [' + EdJoin(Done) +
              '], "missing_uids": [' + EdJoin(Missing) + ']}';
    Spec.Free;
    Sheets.Free;
    Done.Free;
    Missing.Free;
end;

// open_project: open (or find) a project, open its schematic sheets, report whether it is the focused project
function OpenProjectByPath(Path: String): String;
var
    Proj    : IProject;
    Doc     : IDocument;
    i       : Integer;
    Docs    : TStringList;
    Focused : String;
begin
    Proj := GetWorkspace.DM_OpenProject(Path, True);
    if (Proj = nil) then
    begin
        Result := '{"success": false, "error": "' + JSONEscapeString('cannot open ' + Path) + '"}';
        Exit;
    end;
    Docs := TStringList.Create;
    for i := 0 to Proj.DM_LogicalDocumentCount - 1 do
    begin
        Doc := Proj.DM_LogicalDocuments(i);
        if (Doc.DM_DocumentKind = 'SCH') then OpenLibraryDocument('SCH', Doc.DM_FullPath);
        Docs.Add('{"path": "' + JSONEscapeString(Doc.DM_FullPath) + '", "kind": "' + JSONEscapeString(Doc.DM_DocumentKind) + '"}');
    end;
    Focused := '';
    if (GetWorkspace.DM_FocusedProject <> nil) then Focused := GetWorkspace.DM_FocusedProject.DM_ProjectFullPath;
    Result := '{"success": ' + EdBool(UpperCase(Focused) = UpperCase(Proj.DM_ProjectFullPath)) + ', "project": "' +
              JSONEscapeString(Proj.DM_ProjectFullPath) + '", "focused": "' + JSONEscapeString(Focused) +
              '", "documents": [' + EdJoin(Docs) + ']}';
    Docs.Free;
end;

// Entry points for Altium_API's dispatcher
function ExecuteEditSchematic(RequestData: TStringList): String;
begin
    Result := EditSchematicFromSpec(ROOT_DIR + ED_EDIT_SPEC);
end;

function ExecuteAnnotateSchematic(RequestData: TStringList): String;
begin
    Result := AnnotateFromSpec(ROOT_DIR + ED_ANNOTATE_SPEC);
end;

function ExecuteOpenProject(RequestData: TStringList): String;
var
    i : Integer;
    P : String;
begin
    P := '';
    for i := 0 to RequestData.Count - 1 do
        if (Pos('"project_path"', RequestData[i]) > 0) then
            P := JSONLineValue(RequestData[i]);
    if (P = '') then Result := '{"success": false, "error": "no project_path"}'
    else Result := OpenProjectByPath(P);
end;
