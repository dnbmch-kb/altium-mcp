// altium_bridge.pas
// The bridge between the MCP servers and Altium. A server queues a request in the mailbox under
// C:\Users\Public\altium_mcp\ (server/mailbox.py has the layout) and runs Run; Run takes the queued requests of the
// server holding the one-script lock, each from its own folder mail\<id>\, reports progress to that folder's
// progress.log and answers into its response.json with the request's id, once the libraries the request opened and
// then the bridge's own script project are closed. A server started before the mailbox writes request.json at the
// root instead and waits for response.json there; Run still answers that while such a server holds the lock
// (ServeLegacy).

const
    constScriptProjectName = 'Altium_API'; // Define the script project name
    REPLACEALL = 1;
    EXCHANGE_ROOT = 'C:\Users\Public\altium_mcp\';
    LEGACY_MAX_AGE = 130;      // seconds: a single-file request older than this was given up by its server (it waits 120)
    QUEUE_RUNS = 20;           // requests one run serves at most
var
    RequestData : TStringList;
    ResponseData : TStringList;
    Params : TStringList;
    REQUEST_FILE : String;
    RESPONSE_FILE : String;
    ROOT_DIR: String;          // the request's folder: its spec files are read and its temporary files written there
    REQUEST_ID : String;       // '' for a single-file request
    Answered : TStringList;    // the folders of the mailbox requests this run answered; Run renames each answer last

// The pid of the lock's holder ('' when nobody holds it); Legacy is set when the holder is an altium-mcp server
// that speaks the single-file protocol (its lock owner names no mailbox).
function LockHolderPid(var Legacy: Boolean): String;
var
    L    : TStringList;
    S    : String;
    P, Q : Integer;
begin
    Result := '';
    Legacy := False;
    S := '';
    if not FileExists(EXCHANGE_ROOT + 'altium_script.lock') then Exit;
    L := TStringList.Create;
    try
        L.LoadFromFile(EXCHANGE_ROOT + 'altium_script.lock');
        S := L.Text;
    except
        S := '';
    end;
    L.Free;
    P := Pos('"pid":', S);
    if (P = 0) then Exit;
    P := P + 6;
    while (P <= Length(S)) and (S[P] = ' ') do P := P + 1;
    Q := P;
    while (Q <= Length(S)) and (S[Q] >= '0') and (S[Q] <= '9') do Q := Q + 1;
    Result := Copy(S, P, Q - P);
    Legacy := (Pos('"owner": "altium-mcp ', S) > 0) and (Pos('(mailbox)', S) = 0);
end;

// Extract the component pins logic
function ExecuteGetComponentPins(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    DesignatorsList: TStringList;
begin
    DesignatorsList := TStringList.Create;
    try
        // Look through all the RequestData lines to find designators
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Found the designators parameter
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // This is an array element
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);
                    
                    i := i + 1;
                end;
                
                break;
            end;
        end;
        
        if DesignatorsList.Count > 0 then
        begin
            Result := GetComponentPinsFromList(ROOT_DIR, DesignatorsList);
        end
        else
        begin
            LogScriptError('Error: No designators found for get_component_pins');
            Result := '';
        end;
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the create net class logic
function ExecuteCreateNetClass(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    ComponentName: String;
    SourceList: TStringList;
begin
    ComponentName := '';
    SourceList := TStringList.Create;
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for class_name
            if (Pos('"class_name"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ComponentName := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ComponentName := TrimJSON(ComponentName);
            end
            // Look for net_names array
            else if (Pos('"net_names"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the net name
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        SourceList.Add(ParamValue);
                    
                    i := i + 1;
                end;
            end;
        end;
        
        if (ComponentName <> '') and (SourceList.Count > 0) then
        begin
            Result := CreateNetClass(ComponentName, SourceList);
        end
        else
        begin
            if ComponentName = '' then
                Result := '{"success": false, "error": "No class name provided"}'
            else
                Result := '{"success": false, "error": "No net names provided"}';
        end;
    finally
        SourceList.Free;
    end;
end;

// Extract the take view screenshot logic
function ExecuteTakeViewScreenshot(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    ViewType: String;
    DesignatorsList: TStringList;
    Document: String;
begin
    // Extract the view type parameter
    ViewType := 'pcb';  // Default to PCB
    Document := '';
    DesignatorsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for view_type parameter
            if (Pos('"view_type"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                ViewType := ParamValue;
            end
            else if (Pos('"document"', RequestData[i]) > 0) then
                Document := JSONLineValue(RequestData[i])
            // Look for optional designators array to zoom to before capture
            else if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);

                    i := i + 1;
                end;
            end;
        end;

        Result := TakeViewScreenshot(ViewType, DesignatorsList, Document);
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the create schematic symbol logic
function ExecuteCreateSchematicSymbol(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    ComponentName: String;
    PartCount: Integer;
    PinsList: TStringList;
    GraphicsList: TStringList;
begin
    // Look for component name
    ComponentName := '';
    PartCount := 1;  // Default to single-part symbol
    PinsList := TStringList.Create;
    GraphicsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for component name
            if (Pos('"symbol_name"', RequestData[i]) > 0) then
            begin
                ComponentName := JSONLineValue(RequestData[i]);
            end
            // Look for part_count
            else if (Pos('"part_count"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                PartCount := StrToInt(ParamValue);
            end
            // Look for pins array
            else if (Pos('"pins"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the pin data
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    // Unescape JSON backslashes (e.g. \\ -> \ for Altium overbar notation)
                    ParamValue := StringReplace(ParamValue, '\\', '\', REPLACEALL);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        PinsList.Add(ParamValue);

                    i := i + 1;
                end;
            end
            // Look for graphics array
            else if (Pos('"graphics"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        GraphicsList.Add(ParamValue);

                    i := i + 1;
                end;
            end
            // Look for description
            else if (Pos('"description"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := JSONLineValue(RequestData[i]);
                PinsList.Add('Description=' + ParamValue);
            end;
        end;

        if ComponentName <> '' then
        begin
            Result := CreateSchematicSymbol(ComponentName, PinsList, GraphicsList, PartCount);
        end
        else
        begin
            LogScriptError('Error: No component name provided');
            Result := '';
        end;
    finally
        PinsList.Free;
        GraphicsList.Free;
    end;
end;

// Extract the set PCB layer visibility logic
function ExecuteSetPCBLayerVisibility(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    SourceList: TStringList;
    Visible: Boolean;
begin
    // Create a stringlist for layer names and extract the visible parameter
    SourceList := TStringList.Create;
    Visible := False;
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for layer_names array
            if (Pos('"layer_names"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the layer name
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        SourceList.Add(ParamValue);
                    
                    i := i + 1;
                end;
            end
            // Look for visible parameter
            else if (Pos('"visible"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                Visible := (ParamValue = 'true');
            end;
        end;
        
        if SourceList.Count > 0 then
        begin
            Result := SetPCBLayerVisibility(SourceList, Visible);
        end
        else
        begin
            Result := '{"success": false, "error": "No layer names provided"}';
        end;
    finally
        SourceList.Free;
    end;
end;

// Extract the set component position logic
function ExecuteSetComponentPosition(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    Designator: String;
    NewX, NewY: Float;
    Rotation: Float;
begin
    Designator := '';
    NewX := 0;
    NewY := 0;
    Rotation := -1;  // Default -1 means keep current rotation
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designator
            if (Pos('"designator"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                Designator := Trim(ParamValue);
            end
            // Look for x
            else if (Pos('"x"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                NewX := SafeStrToFloat(ParamValue);
            end
            // Look for y
            else if (Pos('"y"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                NewY := SafeStrToFloat(ParamValue);
            end
            // Look for rotation
            else if (Pos('"rotation"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                Rotation := SafeStrToFloat(ParamValue);
            end;
        end;
        
        if Designator <> '' then
        begin
            Result := SetComponentPosition(Designator, NewX, NewY, Rotation);
        end
        else
        begin
            LogScriptError('Error: No designator found for set_component_position');
            Result := '';
        end;
    finally
    end;
end;

// Extract the move components logic
function ExecuteMoveComponents(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    DesignatorsList: TStringList;
    XOffset, YOffset: Integer;
    Rotation: Float;
begin
    // For this command, we need to extract the designators array and the offset values
    DesignatorsList := TStringList.Create;
    XOffset := 0;
    YOffset := 0;
    Rotation := 0;  // Default rotation is 0 (no change)
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designators array
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);
                    
                    i := i + 1;
                end;
            end
            // Look for x_offset
            else if (Pos('"x_offset"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                XOffset := MilsToCoord(SafeStrToFloat(ParamValue));
            end
            // Look for y_offset
            else if (Pos('"y_offset"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                YOffset := MilsToCoord(SafeStrToFloat(ParamValue));
            end
            // Look for rotation
            else if (Pos('"rotation"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                Rotation := SafeStrToFloat(ParamValue);
            end;
        end;
        
        if DesignatorsList.Count > 0 then
        begin
            Result := MoveComponentsByDesignators(DesignatorsList, XOffset, YOffset, Rotation);
        end
        else
        begin
            LogScriptError('Error: No designators found for move_components');
            Result := '';
        end;
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the get footprint primitives logic
function ExecuteGetFootprintPrimitives(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    LibraryPath: String;
    FootprintName: String;
begin
    LibraryPath := '';
    FootprintName := '';

    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"library_path"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            LibraryPath := JSONLineValue(RequestData[i]);
        end
        else if (Pos('"footprint_name"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            FootprintName := JSONLineValue(RequestData[i]);
        end;
    end;

    Result := GetFootprintPrimitives(ROOT_DIR, LibraryPath, FootprintName);
end;

// Extract the create footprints batch logic
function ExecuteCreateFootprintsBatch(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    SpecFile: String;
begin
    SpecFile := '';

    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"spec_file"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            SpecFile := JSONLineValue(RequestData[i]);
        end;
    end;

    if (SpecFile <> '') then
        Result := CreateFootprintsBatch(SpecFile)
    else
        Result := 'ERROR: No spec_file provided for create_footprints_batch';
end;

// Extract the create symbols batch logic
function ExecuteCreateSymbolsBatch(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    SpecFile: String;
begin
    SpecFile := '';

    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"spec_file"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            SpecFile := JSONLineValue(RequestData[i]);
        end;
    end;

    if (SpecFile <> '') then
        Result := CreateSymbolsBatch(SpecFile)
    else
        Result := 'ERROR: No spec_file provided for create_symbols_batch';
end;

// Extract the get symbol primitives logic
function ExecuteGetSymbolPrimitives(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    LibraryPath: String;
    SymbolName: String;
begin
    LibraryPath := '';
    SymbolName := '';

    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        // Look for library_path
        if (Pos('"library_path"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            LibraryPath := JSONLineValue(RequestData[i]);
        end
        // Look for symbol_name
        else if (Pos('"symbol_name"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            SymbolName := JSONLineValue(RequestData[i]);
        end;
    end;

    Result := GetSymbolPrimitives(ROOT_DIR, LibraryPath, SymbolName);
end;

// Extract the get net connections logic
function ExecuteGetNetConnections(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    DesignatorsList: TStringList;
begin
    DesignatorsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designators array (optional - empty means use selection)
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);

                    i := i + 1;
                end;
            end;
        end;

        Result := GetNetConnections(ROOT_DIR, DesignatorsList);
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the check placement logic
function ExecuteCheckPlacement(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    DesignatorsList: TStringList;
    ClearanceMils: Double;
begin
    DesignatorsList := TStringList.Create;
    ClearanceMils := 6;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designators array (optional - empty means use selection)
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);

                    i := i + 1;
                end;
            end
            // Look for clearance_mils
            else if (Pos('"clearance_mils"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := TrimJSON(ParamValue);
                ClearanceMils := SafeStrToFloat(ParamValue);
            end;
        end;

        Result := CheckPlacement(DesignatorsList, ClearanceMils);
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the place components logic
function ExecutePlaceComponents(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    PlacementsList: TStringList;
    BoardPath: String;
    Save: Boolean;
begin
    BoardPath := '';
    Save := False;
    // Placements arrive as an array of pipe-delimited strings
    // ('Designator|X|Y|Rotation|Layer') so the line-based parser stays robust
    PlacementsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"board"', RequestData[i]) > 0) then
                BoardPath := JSONLineValue(RequestData[i]);
            if (Pos('"save"', RequestData[i]) > 0) then
                Save := LowerCase(JSONLineValue(RequestData[i])) = 'true';
            // Look for placements array
            if (Pos('"placements"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the placement value
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        PlacementsList.Add(ParamValue);

                    i := i + 1;
                end;
            end;
        end;

        if PlacementsList.Count > 0 then
        begin
            Result := PlaceComponentsFromList(PlacementsList, BoardPath, Save);
        end
        else
        begin
            Result := 'ERROR: No placements provided for place_components';
        end;
    finally
        PlacementsList.Free;
    end;
end;

// Extract the layout duplicator apply logic
function ExecuteLayoutDuplicatorApply(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    SourceList, DestList: TStringList;
begin
    // For this command, we need to extract the source and destination lists
    SourceList := TStringList.Create;
    DestList := TStringList.Create;
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for source designators array
            if (Pos('"source_designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        SourceList.Add(ParamValue);
                    
                    i := i + 1;
                end;
            end
            // Look for destination designators array
            else if (Pos('"destination_designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DestList.Add(ParamValue);
                    
                    i := i + 1;
                end;
            end
        end;
        
        if (SourceList.Count > 0) and (DestList.Count > 0) then
        begin
            Result := ApplyLayoutDuplicator(SourceList, DestList);
        end
        else
        begin
            LogScriptError('Error: Source or destination lists are empty');
            Result := '{"success": false, "error": "Source or destination lists are empty"}';
        end;
    finally
        SourceList.Free;
        DestList.Free;
    end;
end;

// Function to execute get output job containers
function ExecuteGetOutputJobContainers(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    OutJobPath: String;
begin
    OutJobPath := '';
    
    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"outjob_path"', RequestData[i]) > 0) then
        begin
            // Found the outjob_path parameter
            ParamValue := Copy(RequestData[i], Pos(':', RequestData[i]) + 1, Length(RequestData[i]));
            OutJobPath := JSONLineValue(RequestData[i]);
            break;
        end;
    end;
    
    // Call the appropriate function
    Result := GetOutputJobContainers(ROOT_DIR);
end;

// Function to execute run output jobs
function ExecuteRunOutputJobs(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    ContainersList: TStringList;
    OutJobPath: String;
begin
    ContainersList := TStringList.Create;
    OutJobPath := '';
    
    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"outjob_path"', RequestData[i]) > 0) then
                OutJobPath := JSONLineValue(RequestData[i]);
            if (Pos('"container_names"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')
                
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    // Extract the container name
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        ContainersList.Add(ParamValue);
                    
                    i := i + 1;
                end;
            end;
        end;
        
        if ContainersList.Count > 0 then
        begin
            Result := RunOutputJobs(ContainersList, ROOT_DIR, OutJobPath);
        end
        else
        begin
            LogScriptError('Error: No container names specified');
            Result := '{"success": false, "error": "No container names specified"}';
        end;
    finally
        ContainersList.Free;
    end;
end;

// Extract the create PCB footprint logic
function ExecuteCreatePCBFootprint(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    FootprintName: String;
    Description: String;
    PadsList: TStringList;
    CourtyardXMM: Double;
    CourtyardYMM: Double;
begin
    FootprintName := '';
    Description := '';
    CourtyardXMM := 0;
    CourtyardYMM := 0;
    PadsList := TStringList.Create;

    try
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"footprint_name"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                FootprintName := JSONLineValue(RequestData[i]);
            end
            else if (Pos('"description"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                Description := JSONLineValue(RequestData[i]);
            end
            else if (Pos('"courtyard_x_mm"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := TrimJSON(Copy(RequestData[i], ValueStart, Length(RequestData[i])));
                CourtyardXMM := SafeStrToFloat(ParamValue);
            end
            else if (Pos('"courtyard_y_mm"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := TrimJSON(Copy(RequestData[i], ValueStart, Length(RequestData[i])));
                CourtyardYMM := SafeStrToFloat(ParamValue);
            end
            else if (Pos('"pads"', RequestData[i]) > 0) then
            begin
                i := i + 1;
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        PadsList.Add(ParamValue);
                    i := i + 1;
                end;
            end;
        end;

        if FootprintName <> '' then
            Result := CreatePCBFootprint(FootprintName, Description, PadsList, CourtyardXMM, CourtyardYMM)
        else
            Result := '{"success": false, "error": "No footprint name provided"}';
    finally
        PadsList.Free;
    end;
end;

// Extract the search library symbol logic
function ExecuteSearchLibrarySymbol(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    LibraryPath: String;
    SymbolName: String;
begin
    LibraryPath := '';
    SymbolName := '';

    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        // Look for library_path
        if (Pos('"library_path"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            LibraryPath := JSONLineValue(RequestData[i]);
        end
        // Look for symbol_name
        else if (Pos('"symbol_name"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            SymbolName := JSONLineValue(RequestData[i]);
        end;
    end;

    if SymbolName <> '' then
    begin
        Result := SearchLibrarySymbol(ROOT_DIR, LibraryPath, SymbolName);
    end
    else
    begin
        Result := 'ERROR: No symbol name provided for search_library_symbol';
    end;
end;

// get_erc_violations: optional "compile" parameter (default true)
function ExecuteGetERCViolations(RequestData: TStringList): String;
var
    i: Integer;
    DoCompile: Boolean;
    ParamValue: String;
begin
    DoCompile := True;
    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"compile"', RequestData[i]) > 0) then
        begin
            ParamValue := Copy(RequestData[i], Pos(':', RequestData[i]) + 1, Length(RequestData[i]));
            DoCompile := LowerCase(TrimJSON(ParamValue)) <> 'false';
        end;
    end;
    Result := GetERCViolations(ROOT_DIR, DoCompile);
end;

// get_component_primitives: "designators" array parameter
function ExecuteGetComponentPrimitives(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    DesignatorsList: TStringList;
begin
    DesignatorsList := TStringList.Create;
    try
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                i := i + 1;
                while (i < RequestData.Count) and (Pos(']', RequestData[i]) = 0) do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := StringReplace(ParamValue, '"', '', REPLACEALL);
                    ParamValue := StringReplace(ParamValue, ',', '', REPLACEALL);
                    ParamValue := Trim(ParamValue);
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);
                    i := i + 1;
                end;
                break;
            end;
        end;

        if DesignatorsList.Count > 0 then
            Result := GetComponentPrimitivesFromList(ROOT_DIR, DesignatorsList)
        else
            Result := 'ERROR: No designators given for get_component_primitives';
    finally
        DesignatorsList.Free;
    end;
end;

// get_project_components: optional "include_parameters" (default false)
function ExecuteGetProjectComponents(RequestData: TStringList): String;
var
    i: Integer;
    IncludeParams: Boolean;
    ParamValue: String;
begin
    IncludeParams := False;
    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"include_parameters"', RequestData[i]) > 0) then
        begin
            ParamValue := Copy(RequestData[i], Pos(':', RequestData[i]) + 1, Length(RequestData[i]));
            IncludeParams := LowerCase(TrimJSON(ParamValue)) = 'true';
        end;
    end;
    Result := GetProjectComponents(ROOT_DIR, IncludeParams);
end;

// Function to execute a command with parameters
function ExecuteCommand(CommandName: String): String;
var
    ViewHint   : String;
    ShotDocument : String;
    HintIdx    : Integer;
    HintStart  : Integer;
    HintValue  : String;
begin
    Result := '';

    // take_view_screenshot can target a PCB or a schematic. Pull view_type out
    // of the request so the focus helper does not always demand a PCB; a
    // named document is printed wherever it is and needs no focus.
    ViewHint := '';
    ShotDocument := '';
    if CommandName = 'take_view_screenshot' then
    begin
        for HintIdx := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"view_type"', RequestData[HintIdx]) > 0) then
            begin
                HintStart := Pos(':', RequestData[HintIdx]) + 1;
                HintValue := Copy(RequestData[HintIdx], HintStart,
                                  Length(RequestData[HintIdx]) - HintStart + 1);
                ViewHint := LowerCase(TrimJSON(HintValue));
            end
            else if (Pos('"document"', RequestData[HintIdx]) > 0) then
                ShotDocument := JSONLineValue(RequestData[HintIdx]);
        end;
    end;

    // A focus failure is an answer, not a dialog: WriteResponse turns the
    // 'ERROR: ' prefix into success=false with this message.
    if (ShotDocument = '') and not EnsureDocumentFocused(CommandName, ViewHint) then
    begin
        Result := 'ERROR: ' + FocusFailure;
        Exit;
    end;

    // Direct command execution based on the command name
    case CommandName of
        'get_component_pins':
            Result := ExecuteGetComponentPins(RequestData);            
        'get_all_nets':
            Result := GetAllNets(ROOT_DIR);            
        'create_net_class':
            Result := ExecuteCreateNetClass(RequestData);            
        'get_all_component_data':
            Result := GetAllComponentData(ROOT_DIR, False);            
        'take_view_screenshot':
            Result := ExecuteTakeViewScreenshot(RequestData);            
        'get_library_symbol_reference':
            Result := GetLibrarySymbolReference(ROOT_DIR);            
        'create_schematic_symbol':
            Result := ExecuteCreateSchematicSymbol(RequestData);            
        'get_schematic_data':
            Result := GetSchematicData(ROOT_DIR);            
        'get_pcb_layers':
            Result := GetPCBLayers(ROOT_DIR);            
        'set_pcb_layer_visibility':
            Result := ExecuteSetPCBLayerVisibility(RequestData);   
        'get_pcb_layer_stackup':
            Result := GetPCBLayerStackup(ROOT_DIR);         
        'get_selected_components_coordinates':
            Result := GetSelectedComponentsCoordinates(ROOT_DIR); 
		'set_component_position':
			Result := ExecuteSetComponentPosition(RequestData);
        'move_components':
            Result := ExecuteMoveComponents(RequestData);
        'place_components':
            Result := ExecutePlaceComponents(RequestData);
        'check_placement':
            Result := ExecuteCheckPlacement(RequestData);
        'get_net_connections':
            Result := ExecuteGetNetConnections(RequestData);
        'get_symbol_primitives':
            Result := ExecuteGetSymbolPrimitives(RequestData);
        'create_symbols_batch':
            Result := ExecuteCreateSymbolsBatch(RequestData);
        'build_circuit':
            Result := BuildCircuitFromSpec(ROOT_DIR + 'circuit_spec.txt',
                                          EXCHANGE_ROOT + 'param_placement.txt');
        'get_footprint_primitives':
            Result := ExecuteGetFootprintPrimitives(RequestData);
        'create_footprints_batch':
            Result := ExecuteCreateFootprintsBatch(RequestData);
        'layout_duplicator':
            Result := GetLayoutDuplicatorComponents(True);            
        'layout_duplicator_apply':
            Result := ExecuteLayoutDuplicatorApply(RequestData);            
        'get_pcb_rules':
            Result := GetPCBRules(ROOT_DIR);
        'get_output_job_containers':
            Result := ExecuteGetOutputJobContainers(RequestData);
        'run_output_jobs':
            Result := ExecuteRunOutputJobs(RequestData);
        'search_library_symbol':
            Result := ExecuteSearchLibrarySymbol(RequestData);
        'create_pcb_footprint':
            Result := ExecuteCreatePCBFootprint(RequestData);
        'get_project_info':
            Result := GetProjectInfo(ROOT_DIR);
        'get_project_components':
            Result := ExecuteGetProjectComponents(RequestData);
        'get_erc_violations':
            Result := ExecuteGetERCViolations(RequestData);
        'get_board_summary':
            Result := GetBoardSummary(ROOT_DIR);
        'get_component_primitives':
            Result := ExecuteGetComponentPrimitives(RequestData);
        'edit_schematic':
            Result := ExecuteEditSchematic(RequestData);
        'annotate_schematic':
            Result := ExecuteAnnotateSchematic(RequestData);
        'open_project':
            Result := ExecuteOpenProject(RequestData);
    else
        LogScriptError('Error: Unknown command: ' + CommandName);
    end;
end;

// Function to extract a parameter name-value pair from a JSON line
procedure ExtractParameter(Line: String);
var
    ParamName: String;
    ParamValue: String;
    NameEnd: Integer;
    ValueStart: Integer;
begin
    // Skip command line and lines without a colon
    if (Pos('"command":', Line) > 0) or (Pos(':', Line) = 0) then
        Exit;

    // Find the parameter name
    NameEnd := Pos(':', Line) - 1;
    if NameEnd <= 0 then Exit;

    // Extract and clean the parameter name
    ParamName := Copy(Line, 1, NameEnd);
    ParamName := TrimJSON(ParamName);

    // Extract the parameter value - don't trim arrays
    ValueStart := Pos(':', Line) + 1;
    ParamValue := Copy(Line, ValueStart, Length(Line) - ValueStart + 1);

    // Decode it only if it's not an array (keeps commas, decodes escapes)
    if (Pos('[', ParamValue) = 0) then
        ParamValue := JSONLineValue(Line);

    // Add to parameters list
    if (ParamName <> '') and (ParamName <> 'command') then
        Params.Add(ParamName + '=' + ParamValue);
end;

procedure WriteResponse(Success: Boolean; Data: String; ErrorMsg: String);
var
    ActualSuccess: Boolean;
    ActualErrorMsg: String;
    ResultProps: TStringList;
begin
    // Check if Data contains an error message
    if (Pos('ERROR:', Data) = 1) then
    begin
        ActualSuccess := False;
        ActualErrorMsg := Copy(Data, 8, Length(Data)); // Remove 'ERROR: ' prefix
    end
    else
    begin
        ActualSuccess := Success;
        ActualErrorMsg := ErrorMsg;
    end;

    // Create response props
    ResultProps := TStringList.Create;
    ResponseData := TStringList.Create;
    
    try
        // Add properties
        AddJSONBoolean(ResultProps, 'success', ActualSuccess);
        if (REQUEST_ID <> '') then AddJSONProperty(ResultProps, 'request_id', REQUEST_ID);
        
        if ActualSuccess then
        begin
            // For JSON responses (starting with [ or {), don't wrap in additional quotes
            if (Length(Data) > 0) and ((Data[1] = '[') or (Data[1] = '{')) then
                ResultProps.Add(JSONPairStr('result', Data, False))
            else
                AddJSONProperty(ResultProps, 'result', Data);
        end
        else
        begin
            AddJSONProperty(ResultProps, 'error', ActualErrorMsg);
        end;
        
        // Build response: a mailbox answer appears whole, renamed into place by Run once the bridge's script project
        // is closed, so the server never reads half of it and holds the lock until the bridge is done
        ResponseData.Text := BuildJSONObject(ResultProps);
        if (REQUEST_ID <> '') then
        begin
            ResponseData.SaveToFile(ROOT_DIR + 'response.tmp');
            BridgeProgress('answered', 0, 0);
            Answered.Add(ROOT_DIR);
        end
        else
            ResponseData.SaveToFile(RESPONSE_FILE);
    finally
        ResultProps.Free;
        ResponseData.Free;
    end;
end;

// Read REQUEST_FILE, run its command and answer into RESPONSE_FILE. The libraries the command opened are closed before
// the answer, a failed command's too (OpenLibraryDocument, CloseOpenedLibraries).
procedure HandleRequest;
var
    CommandType: String;
    Result: String;
    i: Integer;
    Line: String;
    ValueStart: Integer;
begin
    // Check if request file exists
    if not FileExists(REQUEST_FILE) then
    begin
        LogScriptError('Error: No request file found at ' + REQUEST_FILE);
        Exit;
    end;

    OpenedLibraries := TStringList.Create;
    try
        // Initialize parameters list
        Params := TStringList.Create;
        Params.Delimiter := '=';

        // Read the request file
        RequestData := TStringList.Create;
        try
            RequestData.LoadFromFile(REQUEST_FILE);

            // Default command type
            CommandType := '';

            // Parse command and parameters
            for i := 0 to RequestData.Count - 1 do
            begin
                Line := RequestData[i];

                // Extract command
                if Pos('"command":', Line) > 0 then
                begin
                    ValueStart := Pos(':', Line) + 1;
                    CommandType := Copy(Line, ValueStart, Length(Line) - ValueStart + 1);
                    CommandType := TrimJSON(CommandType);
                end
                else
                begin
                    // Extract all other parameters
                    ExtractParameter(Line);
                end;
            end;

            // Execute the command if valid
            if CommandType <> '' then
            begin
                Result := ExecuteCommand(CommandType);
                CloseOpenedLibraries;

                if Result <> '' then
                begin
                    WriteResponse(True, Result, '');
                end
                else
                begin
                    WriteResponse(False, '', 'Command execution failed');
                    LogScriptError('Error: Command execution failed');
                end;
            end
            else
            begin
                WriteResponse(False, '', 'No command specified');
                LogScriptError('Error: No command specified');
            end;
        finally
            RequestData.Free;
            Params.Free;
        end;
    except
        // Simple exception handling without the specific exception type
        CloseOpenedLibraries;
        WriteResponse(False, '', 'Exception occurred during script execution');
        LogScriptError('Error: Exception occurred during script execution');
    end;
    OpenedLibraries.Free;
end;

// One mailbox request, already taken: its folder is mail\<Id>\
procedure ServeRequest(Id: String);
begin
    REQUEST_ID := Id;
    ROOT_DIR := EXCHANGE_ROOT + 'mail\' + Id + '\';
    REQUEST_FILE := ROOT_DIR + 'request.json';
    RESPONSE_FILE := ROOT_DIR + 'response.json';
    BridgeProgressBegin(ROOT_DIR + 'progress.log');
    BridgeProgress('taken', 0, 0);
    HandleRequest;
    BridgeProgressEnd;
    REQUEST_ID := '';
end;

// Take and serve the queued requests of the lock's holder, oldest first; a queued request of anyone else was
// left by a server that no longer holds the lock and is never run. Taking is a rename of queue\<id>.req into
// the request's folder, so a request is taken once. Returns how many were served.
function ServeQueue(HolderPid: String): Integer;
var
    Files : TStringList;
    i     : Integer;
    Id    : String;
    Taken : Boolean;
begin
    Result := 0;
    if (HolderPid = '') or not DirectoryExists(EXCHANGE_ROOT + 'queue') then Exit;
    repeat
        Taken := False;
        Files := TStringList.Create;
        FindFiles(EXCHANGE_ROOT + 'queue\', '*.req', faAnyFile, False, Files);
        Files.Sort;
        for i := 0 to Files.Count - 1 do
        begin
            Id := ExtractFileName(Files[i]);
            Id := Copy(Id, 1, Length(Id) - 4);
            if (Copy(Id, 1, Length(HolderPid) + 1) = HolderPid + '-') and
               DirectoryExists(EXCHANGE_ROOT + 'mail\' + Id) and
               RenameFile(Files[i], EXCHANGE_ROOT + 'mail\' + Id + '\taken') then
            begin
                ServeRequest(Id);
                Result := Result + 1;
                Taken := True;
                Break;
            end;
        end;
        Files.Free;
    until (not Taken) or (Result >= QUEUE_RUNS);
end;

// A server started before the mailbox: request.json at the root, unanswered (it deletes response.json before it
// writes the request) and younger than its server's wait
procedure ServeLegacy;
begin
    REQUEST_ID := '';
    ROOT_DIR := EXCHANGE_ROOT;
    REQUEST_FILE := ROOT_DIR + 'request.json';
    RESPONSE_FILE := ROOT_DIR + 'response.json';
    if not FileExists(REQUEST_FILE) or FileExists(RESPONSE_FILE) then Exit;
    if ((Now - FileDateToDateTime(FileAge(REQUEST_FILE))) * 86400 > LEGACY_MAX_AGE) then Exit;
    HandleRequest;
end;

// Main procedure to run the bridge. Last, the bridge's script project leaves the operator's workspace (house rule:
// after a release it holds nothing an agent opened): every copy open under bridge\, this run's own and any an older
// run left, is closed by its path the way tools/altium/altium_run.py's closer closes a staged project - focused with
// one of its units shown, then WorkspaceManager:CloseObject - and the document focused before is shown again. A script
// runs on after its own project closes (#Notes/guides/altium_scripting.md), so from the first close on Run calls only
// Altium's routines and its own locals, nothing of this project's units: each answered request's folder gets the
// outcome as closed.txt, then its answer is renamed into place, so its server holds the lock until the bridge is done.
procedure Run;
var
    Holder  : String;
    Legacy  : Boolean;
    Wsp     : IWorkspace;
    Prj     : IProject;
    Doc     : IServerDocument;
    Answers, Copies, Outcome : TStringList;
    Stage, Focused : String;
    I       : Integer;
begin
    REQUEST_ID := '';
    ROOT_DIR := EXCHANGE_ROOT;
    PROGRESS_FILE := '';
    if not DirectoryExists(EXCHANGE_ROOT) then ForceDirectories(EXCHANGE_ROOT);
    Answered := TStringList.Create;
    Holder := LockHolderPid(Legacy);
    if (ServeQueue(Holder) = 0) and Legacy then ServeLegacy;

    Answers := Answered;
    Stage := UpperCase(EXCHANGE_ROOT + 'bridge\');
    Wsp := GetWorkspace;
    Focused := '';
    if (Wsp.DM_FocusedDocument <> nil) then Focused := Wsp.DM_FocusedDocument.DM_FullPath;
    Copies := TStringList.Create;
    Outcome := TStringList.Create;
    for I := 0 to Wsp.DM_ProjectCount - 1 do
        if (Pos(Stage, UpperCase(Wsp.DM_Projects(I).DM_ProjectFullPath)) = 1) then
            Copies.Add(Wsp.DM_Projects(I).DM_ProjectFullPath);
    for I := 0 to Copies.Count - 1 do
    begin
        Prj := Wsp.DM_GetProjectFromPath(Copies[I]);
        if (Prj <> nil) then
        begin
            Prj.DM_SetAsCurrentProject;
            if (Prj.DM_LogicalDocumentCount > 0) then
            begin
                Doc := Client.OpenDocument(Prj.DM_LogicalDocuments(0).DM_DocumentKind,
                                           Prj.DM_LogicalDocuments(0).DM_FullPath);
                if (Doc <> nil) then Client.ShowDocument(Doc);
            end;
            if (Wsp.DM_FocusedProject = nil) then
                Outcome.Add('REFUSED ' + Copies[I] + ': no focused project')
            else if (UpperCase(Wsp.DM_FocusedProject.DM_ProjectFullPath) <> UpperCase(Copies[I])) then
                Outcome.Add('REFUSED ' + Copies[I] + ': the focused project is ' +
                            Wsp.DM_FocusedProject.DM_ProjectFullPath)
            else
            begin
                ResetParameters;
                AddStringParameter('ObjectKind', 'Project');
                RunProcess('WorkspaceManager:CloseObject');
                if (Wsp.DM_GetProjectFromPath(Copies[I]) = nil) then
                    Outcome.Add('CLOSED ' + Copies[I])
                else
                    Outcome.Add('STILL OPEN ' + Copies[I]);
            end;
        end;
    end;
    if (Focused <> '') then
    begin
        Doc := Client.GetDocumentByPath(Focused);
        if (Doc <> nil) then Client.ShowDocument(Doc);
    end;
    for I := 0 to Answers.Count - 1 do
    begin
        Outcome.SaveToFile(Answers[I] + 'closed.txt');
        RenameFile(Answers[I] + 'response.tmp', Answers[I] + 'response.json');
    end;
    Answers.Free;
    Copies.Free;
    Outcome.Free;
end;


