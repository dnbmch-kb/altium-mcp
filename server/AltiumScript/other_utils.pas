const
    DEFAULT = 'Blank';
    SCRIPT_ERROR_LOG = 'C:\Users\Public\altium_mcp\script_errors.log';

    // One layer of a PCB Print (PrintView); %L% is the layer's name
    PRINT_LAYER_RECORD = 'CArc=Full|CFill=Full|Comment=Full|Coordinate=Full|CPad=Full|CRegion=Full|CText=Full|CTrack=Full|CVia=Full|Designator=Full|Dimension=Full|DLayer1=TopLayer|DLayer2=BottomLayer|DrillType=Regular|FArc=Full|FFill=Full|FPad=Full|FRegion=Full|FText=Full|FTrack=Full|FVia=Full|Layer=%L%|Polygon=Full|PrintOutIndex=0|Record=PcbPrintLayer';

var
    // Why the last EnsureDocumentFocused call failed; reported to the server.
    FocusFailure : String;
    // The running mailbox request's progress (BridgeProgress): '' while a single-file request runs, which has none
    PROGRESS_FILE : String;
    ProgressFile  : TextFile;
    ProgressSeq   : Integer;
    ProgressT0    : Integer;
    // The libraries the running request opened (OpenLibraryDocument); HandleRequest closes them before it answers
    OpenedLibraries : TStringList;

// Errors must never be modal. The server is driven by an agent with no hands:
// a ShowMessage box blocks this script before it writes its response, every
// later request then times out, and Altium reports "another instance is
// busy" until a human clicks OK. Log instead; the caller returns the error.
procedure LogScriptError(Msg: String);
var
    L : TStringList;
begin
    L := TStringList.Create;
    try
        if FileExists(SCRIPT_ERROR_LOG) then L.LoadFromFile(SCRIPT_ERROR_LOG);
        L.Add(FormatDateTime('yyyy-mm-dd hh:nn:ss', Now) + '  ' + Msg);
        while L.Count > 500 do L.Delete(0);
        L.SaveToFile(SCRIPT_ERROR_LOG);
    except
    end;
end;

// Progress records for the server waiting on this request (server/mailbox.py): one line each,
// seq|elapsed ms|step|count|total, appended and flushed. The server waits while records keep landing and gives up
// only when none lands for its stall time, so a long batch reports each step. The file is created and opened once per
// request, while nothing can hold it, and every record goes through that handle, which shares reading: a reader holding
// the file open (the server, a watcher) cannot fail a write. A file recreated per record fails while any reader holds
// it: EFCreateError, whose box stopped a drawing pass (ugv-pcb SCB capture, 2026-10-26).
procedure BridgeProgressBegin(Path: String);
begin
    PROGRESS_FILE := Path;
    ProgressSeq := 0;
    ProgressT0 := GetTickCount;
    AssignFile(ProgressFile, PROGRESS_FILE);
    Rewrite(ProgressFile);
end;

procedure BridgeProgress(Step: String; Count: Integer; Total: Integer);
begin
    if (PROGRESS_FILE = '') then Exit;
    ProgressSeq := ProgressSeq + 1;
    WriteLn(ProgressFile, IntToStr(ProgressSeq) + '|' + IntToStr(GetTickCount - ProgressT0) + '|' +
                          StringReplace(Step, '|', '/', REPLACEALL) + '|' + IntToStr(Count) + '|' + IntToStr(Total));
    Flush(ProgressFile);
end;

procedure BridgeProgressEnd;
begin
    if (PROGRESS_FILE <> '') then CloseFile(ProgressFile);
    PROGRESS_FILE := '';
end;

{..............................................................................}
{ Get path of this script project.                                             }
{ Get prj path from Jeff Collins and William Kitchen's stripped down version   }
{..............................................................................}
function ScriptProjectPath(Workspace: IWorkspace) : String;
var
  Project   : IProject;
  scriptsPath : TDynamicString;
  candidatePath : TDynamicString;
  rootDir : TDynamicString;
  projectCount : Integer;
  i      : Integer;
begin
  if (Workspace = nil) then begin result:=''; exit; end;
  { Get a count of the number of currently opened projects.  The script project
    from which this script runs must be one of these. }
  projectCount := Workspace.DM_ProjectCount();
  { Loop over all the open projects.  We're looking for constScriptProjectName
    (of which we are a part).  Once we find this, we want to record the
    path to the script project directory.
    If multiple projects match (e.g. stale copies cached by Altium), prefer
    the one whose ROOT_DIR contains request.json — since the MCP server
    writes request.json before launching, only the active copy will have it. }
  scriptsPath:='';
  for i:=0 to projectCount-1 do
  begin
    { Get reference to project # i. }
    Project := Workspace.DM_Projects(i);
    { See if we found our script project. }
    if (AnsiPos(constScriptProjectName, Project.DM_ProjectFullPath) > 0) then
    begin
      { Strip off project name to give us just the path. }
      candidatePath := StringReplace(Project.DM_ProjectFullPath, '\' +
      constScriptProjectName + '.PrjScr','', MkSet(rfReplaceAll,rfIgnoreCase));

      { Check if request.json exists at this candidate's ROOT_DIR }
      rootDir := ExtractFilePath(ExtractFilePath(candidatePath));
      if FileExists(rootDir + 'request.json') then
      begin
        { Found the active copy — use it immediately }
        result := candidatePath;
        exit;
      end;

      { Keep as fallback in case no candidate has request.json }
      if scriptsPath = '' then
        scriptsPath := candidatePath;
    end;
  end;
  result := scriptsPath;
end;

// Find the first open OutJob document
function GetOpenOutputJob(): String;
var
    Project     : IProject;
    ProjectIdx, I  : Integer;
    Doc: IDocument;
    DocKind: String;
begin
    result := '';
    // The focused OutJob first: with several projects open, the first project's OutJob is not the one meant
    Doc := GetWorkspace.DM_FocusedDocument;
    if (Doc <> Nil) and (Doc.DM_DocumentKind = 'OUTPUTJOB') then
    begin
        result := Doc.DM_FullPath;
        Exit;
    end;
    for ProjectIdx := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Project := GetWorkspace.DM_Projects(ProjectIdx);
        if Project = Nil then Exit;

        // Iterate all documents in the project
        for I := 0 to Project.DM_LogicalDocumentCount - 1 do
        begin
            Doc := Project.DM_LogicalDocuments(I);
            DocKind := Doc.DM_DocumentKind;
            if DocKind = 'OUTPUTJOB' then
            begin
                result := Doc.DM_FullPath;
                Exit;
            end;
        end;
    end;
end;

// Return the Index-th (0-based) field of a pipe-delimited string,
// e.g. GetFieldFromPipeString('R1|100|200', 1) = '100'.
// Returns '' if the field does not exist.
// The bridge's own helper, not an Altium built-in: a script run outside the bridge (the sandbox, a target script)
// finds it undeclared and copies it in.
function GetFieldFromPipeString(S: String; Index: Integer): String;
var
    i, FieldStart, FieldIndex: Integer;
begin
    Result := '';
    FieldIndex := 0;
    FieldStart := 1;
    for i := 1 to Length(S) do
    begin
        if S[i] = '|' then
        begin
            if FieldIndex = Index then
            begin
                Result := Copy(S, FieldStart, i - FieldStart);
                Exit;
            end;
            FieldIndex := FieldIndex + 1;
            FieldStart := i + 1;
        end;
    end;
    if FieldIndex = Index then
        Result := Copy(S, FieldStart, Length(S) - FieldStart + 1);
end;

// Modify the EnsureDocumentFocused function to handle all document types
// and return more detailed information.
// ViewTypeHint is only consulted by commands that can target more than one
// document kind (currently take_view_screenshot); pass '' otherwise.
function EnsureDocumentFocused(CommandName: String; ViewTypeHint: String): Boolean;
var
    I           : Integer;
    P           : Integer;
    Project     : IProject;
    Doc         : IDocument;
    DocFound    : Boolean;
    CurrentDoc  : IServerDocument;
    DocumentKind: String;
    LogMessage  : String;
    OutJobPath: String;
begin
    Result := False;
    DocFound := False;
    DocumentKind := 'PCB'; // Default

    // Commands that handle their own document management - skip focusing
    if (CommandName = 'search_library_symbol') or
       (CommandName = 'build_circuit') or
       (CommandName = 'get_symbol_primitives') or
       (CommandName = 'create_symbols_batch') or
       (CommandName = 'get_footprint_primitives') or
       (CommandName = 'create_footprints_batch') or
       (CommandName = 'create_pcb_footprint') or
       (CommandName = 'get_project_info') or
       (CommandName = 'get_project_components') or
       (CommandName = 'get_erc_violations') or
       (CommandName = 'edit_schematic') or
       (CommandName = 'annotate_schematic') or
       (CommandName = 'open_project') or
       (CommandName = 'run_output_jobs') then
    begin
        Result := True;
        Exit;
    end;

    // For PCB-related commands, ensure PCB is available first
    if (CommandName = 'create_net_class')                    or
       (CommandName = 'get_all_component_data')              or
       (CommandName = 'get_all_components')                  or
       (CommandName = 'get_all_nets')                        or
       (CommandName = 'get_component_pins')                  or
       (CommandName = 'get_pcb_layers')                      or
       (CommandName = 'get_pcb_rules')                       or
       (CommandName = 'get_selected_components_coordinates') or
       (CommandName = 'layout_duplicator')                   or
       (CommandName = 'layout_duplicator_apply')             or
       (CommandName = 'move_components')                     or
       (CommandName = 'place_components')                    or
       (CommandName = 'check_placement')                     or
       (CommandName = 'get_net_connections')                 or
       (CommandName = 'set_component_position')              or
       (CommandName = 'set_pcb_layer_visibility')            or
       (CommandName = 'get_pcb_layer_stackup')               or
       (CommandName = 'get_board_summary')                   or
       (CommandName = 'get_component_primitives')            then
    begin
        DocumentKind := 'PCB';
    end
    // Screenshots can target either domain - follow the caller's view_type
    // instead of always demanding a PCB.
    else if (CommandName = 'take_view_screenshot') then
    begin
        if LowerCase(ViewTypeHint) = 'sch' then
            DocumentKind := 'SCH'
        else
            DocumentKind := 'PCB';
    end
    else if (CommandName = 'create_schematic_symbol')        or
            (CommandName = 'get_library_symbol_reference')   then
    begin
        DocumentKind := 'SCHLIB';
    end
    else if (CommandName = 'create_pcb_footprint') then
    begin
        DocumentKind := 'PCBLIB';
    end
    else if (CommandName = 'get_schematic_data')             then
    begin
        DocumentKind := 'SCH';
    end
    else if (CommandName = 'get_output_job_containers')       or
            (CommandName = 'run_output_jobs')                then
    begin
        DocumentKind := 'OUTJOB';
    end;
    // Default to user argument if command not recognized

    LogMessage := 'Attempting to focus ' + DocumentKind + ' document';
    
    // Log the current focused document first
    if DocumentKind = 'PCB' then
    begin
        if PCBServer <> nil then
            LogMessage := LogMessage + '. Current PCB: ' + BoolToStr(GetBoardSafe(0) <> nil, True);
    end
    else if DocumentKind = 'SCH' then
    begin
        if SchServer <> nil then
            LogMessage := LogMessage + '. Current SCH: ' + BoolToStr(SchServer.GetCurrentSchDocument <> nil, True);
    end
    else if DocumentKind = 'SCHLIB' then
    begin
        if SchServer <> nil then
        begin
            CurrentDoc := SchServer.GetCurrentSchDocument;
            LogMessage := LogMessage + '. Current SCHLIB: ' + BoolToStr((CurrentDoc <> nil) and (CurrentDoc.ObjectID = eSchLib), True);
        end;
    end;
    
    // ShowMessage(LogMessage); // For debugging
    
    FocusFailure := '';
    // Retrieve the current project
    Project := GetWorkspace.DM_FocusedProject;
    If Project = Nil Then
    begin
        FocusFailure := 'No project is open';
        Exit;
    end;

    // Check if the correct document type is already focused
    if (DocumentKind = 'PCB') and (PCBServer <> Nil) then
    begin
        if GetBoardSafe(0) <> Nil then
        begin
            Result := True;
            Exit;
        end;
    end
    else if (DocumentKind = 'SCH') and (SchServer <> Nil) then
    begin
        CurrentDoc := SchServer.GetCurrentSchDocument;
        if CurrentDoc <> Nil then
        begin
            Result := True;
            Exit;
        end;
    end
    else if (DocumentKind = 'SCHLIB') and (SchServer <> Nil) then
    begin
        CurrentDoc := SchServer.GetCurrentSchDocument;
        if (CurrentDoc <> Nil) and (CurrentDoc.ObjectId = eSchLib) then
        begin
            Result := True;
            Exit;
        end;
    end
    else if (DocumentKind = 'PCBLIB') and (PCBServer <> Nil) then
    begin
        if GetPcbLibSafe(0) <> Nil then
        begin
            Result := True;
            Exit;
        end;
    end
    else if (DocumentKind = 'OUTJOB') then
    begin
        OutJobPath := GetOpenOutputJob();
        if OutJobPath <> '' then
        begin
            Result := True;
            Exit;
        end;
    end;

    // Try to find and focus the required document type. The focused project
    // is searched first, then every other open project: after a script run
    // the focused project is often the script project itself, which has no
    // design documents at all.
    For P := -1 to GetWorkspace.DM_ProjectCount - 1 Do
    Begin
      if P < 0 then
        Project := GetWorkspace.DM_FocusedProject
      else
      begin
        Project := GetWorkspace.DM_Projects(P);
        if (Project = Nil) or (Project = GetWorkspace.DM_FocusedProject) then Continue;
      end;
      For I := 0 to Project.DM_LogicalDocumentCount - 1 Do
      Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc.DM_DocumentKind = DocumentKind Then
        Begin
            DocFound := True;
            // Try to open and focus the document
            Doc.DM_OpenAndFocusDocument;
            // Give it a moment to focus
            Sleep(500);

            // Verify that the document is now focused
            if DocumentKind = 'PCB' then
            begin
                if GetBoardSafe(0) <> Nil then
                begin
                    Result := True;
                    // ShowMessage('Successfully focused PCB document');
                    Exit;
                end;
            end
            else if DocumentKind = 'SCH' then
            begin
                CurrentDoc := SchServer.GetCurrentSchDocument;
                if (CurrentDoc <> Nil) then
                begin
                    Result := True;
                    // ShowMessage('Successfully focused SCH document');
                    Exit;
                end;
            end
            else if DocumentKind = 'SCHLIB' then
            begin
                CurrentDoc := SchServer.GetCurrentSchDocument;
                if (CurrentDoc <> Nil) and (CurrentDoc.ObjectID = eSchLib) then
                begin
                    Result := True;
                    // ShowMessage('Successfully focused SCHLIB document');
                    Exit;
                end;
            end
            else if DocumentKind = 'PCBLIB' then
            begin
                if GetPcbLibSafe(0) <> Nil then
                begin
                    Result := True;
                    Exit;
                end;
            end
            else if DocumentKind = 'OUTJOB' then
            begin
                CurrentDoc := SchServer.GetCurrentSchDocument;
                if (CurrentDoc <> Nil) then
                begin
                    Result := True;
                    Exit;
                end;
            end;
        End;
      End;
    End;

    // TODO: Do I want to iterate through all workspace projects to find valid document if it is not current document?
    // Could use IWorkspace.DM_ProjectCount and for loop

    // No matching document found or couldn't be focused
    if not DocFound then
        FocusFailure := 'No ' + DocumentKind + ' document found in any open project'
    else
        FocusFailure := 'Found a ' + DocumentKind + ' document but could not focus it';
    LogScriptError('EnsureDocumentFocused(' + CommandName + '): ' + FocusFailure);
    
    Result := False;
end;

// Zoom the current PCB view to the union bounding box of the given
// components (plus a margin) so a following screenshot shows them clearly.
// Returns the number of components found.
function ZoomToComponents(DesignatorsList: TStringList): Integer;
var
    Board      : IPCB_Board;
    Component  : IPCB_Component;
    Rect       : TCoordRect;
    i          : Integer;
    HaveBounds : Boolean;
    MinX, MinY, MaxX, MaxY : TCoord;
    Margin     : TCoord;
begin
    Result := 0;
    HaveBounds := False;

    Board := GetBoardSafe(0);
    if (Board = nil) then Exit;

    for i := 0 to DesignatorsList.Count - 1 do
    begin
        Component := Board.GetPcbComponentByRefDes(Trim(DesignatorsList[i]));
        if (Component <> nil) then
        begin
            Rect := Component.BoundingRectangleNoNameComment;
            if not HaveBounds then
            begin
                MinX := Rect.Left;
                MaxX := Rect.Right;
                MinY := Rect.Bottom;
                MaxY := Rect.Top;
                HaveBounds := True;
            end
            else
            begin
                if Rect.Left < MinX then MinX := Rect.Left;
                if Rect.Right > MaxX then MaxX := Rect.Right;
                if Rect.Bottom < MinY then MinY := Rect.Bottom;
                if Rect.Top > MaxY then MaxY := Rect.Top;
            end;
            Result := Result + 1;
        end;
    end;

    if HaveBounds then
    begin
        // 10% margin on the larger span, at least 100 mils
        Margin := (MaxX - MinX);
        if (MaxY - MinY) > Margin then Margin := (MaxY - MinY);
        Margin := Margin div 10;
        if Margin < MilsToCoord(100) then Margin := MilsToCoord(100);

        Board.GraphicalView_ZoomOnRect(MinX - Margin, MinY - Margin, MaxX + Margin, MaxY + Margin);
        Board.GraphicalView_ZoomRedraw;
    end;
end;

// The window-grab protocol of servers started before the print path: focus the document kind, zoom a PCB view to
// DesignatorsList, report what is focused; the server then grabs Altium's window.
function TakeViewFocus(ViewType: String; DesignatorsList: TStringList): String;
var
    Board          : IPCB_Board;
    SchDoc         : ISch_Document;
    ResultProps    : TStringList;
    OutputLines    : TStringList;
    ClassName      : String;
    DocType        : String;
    WindowFound    : Boolean;
    ZoomedCount    : Integer;
    FocusedKind    : String;
    FocusedName    : String;

    // For screenshot thread
    ThreadStarted  : Boolean;
    ScreenshotResult : String;
begin
    // Default result
    Result := '{"success": false, "error": "Failed to initialize screenshot capture"}';

    // Determine what type of document we need to focus
    if LowerCase(ViewType) = 'pcb' then
    begin
        DocType := 'PCB';
        ClassName := 'View_Graphical';
    end
    else if LowerCase(ViewType) = 'sch' then
    begin
        DocType := 'SCH';
        ClassName := 'SchView';
    end
    else
    begin
        Result := '{"success": false, "error": "Invalid view type: ' + ViewType + '. Must be ''pcb'' or ''sch''"}';
        Exit;
    end;

    // Optionally zoom to the requested components before capture
    ZoomedCount := 0;
    if (DocType = 'PCB') and (DesignatorsList <> nil) and (DesignatorsList.Count > 0) then
        ZoomedCount := ZoomToComponents(DesignatorsList);

    // Report what is ACTUALLY focused now that EnsureDocumentFocused has run.
    // The server compares this against the requested view: if no document of
    // the requested kind is open, the focus stays where it was and the
    // capture would otherwise silently show the wrong editor.
    FocusedKind := '';
    FocusedName := '';
    if (Client.CurrentView <> nil) and (Client.CurrentView.OwnerDocument <> nil) then
    begin
        FocusedKind := Client.CurrentView.OwnerDocument.Kind;
        FocusedName := ExtractFileName(Client.CurrentView.OwnerDocument.FileName);
    end;

    // Create result JSON
    ResultProps := TStringList.Create;
    try
        // Add successful result properties
        AddJSONBoolean(ResultProps, 'success', True);
        AddJSONProperty(ResultProps, 'view_type', ViewType);
        AddJSONProperty(ResultProps, 'focused_kind', FocusedKind);
        AddJSONProperty(ResultProps, 'focused_document', FocusedName);
        AddJSONProperty(ResultProps, 'class_filter', ClassName);
        AddJSONBoolean(ResultProps, 'window_found', WindowFound);
        AddJSONInteger(ResultProps, 'zoomed_component_count', ZoomedCount);
        
        // Add signal to the server that it can now capture the screenshot
        AddJSONBoolean(ResultProps, 'ready_for_capture', True);

        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
    end;
end;

// Get all available output job containers from the first open OutJob
function GetOutputJobContainers(ROOT_DIR: String): String;
var
    OutJobPath: String;
    IniFile: TIniFile;
    ContainerName, ContainerAction: String;
    G, J: Integer;
    S: String;
    ResultProps: TStringList;
    ContainersArray: TStringList;
    ContainerProps: TStringList;
    OutputLines: TStringList;
begin
    // Get the path of the first open OutJob
    OutJobPath := GetOpenOutputJob();

    // Exit if no open OutJob was found
    if OutJobPath = '' then
    begin
        ResultProps := TStringList.Create;
        try
            AddJSONBoolean(ResultProps, 'success', False);
            AddJSONProperty(ResultProps, 'error', 'No open OutJob document found');
            Result := BuildJSONObject(ResultProps);
        finally
            ResultProps.Free;
        end;
        Exit;
    end;

    // Create output containers array
    ResultProps := TStringList.Create;
    ContainersArray := TStringList.Create;

    try
        // Add the OutJob path to the result
        AddJSONProperty(ResultProps, 'outjob_path', OutJobPath);

        // Open the OutJob file (it's just an INI file)
        IniFile := TIniFile.Create(OutJobPath);
        try
            G := 1; // Group Number
            J := 1; // Job/Container Number
            ContainerName := '';

            // Iterate each Output Group
            While (G = 1) Or (ContainerName <> DEFAULT) Do
            Begin
                S := 'OutputGroup'+IntToStr(G); // Section (aka OutputGroup)

                // Reset J for each group
                J := 1;
                ContainerName := '';
                ContainerAction := '';

                // Iterate each Output Container
                While (J = 1) Or (ContainerName <> DEFAULT) Do
                Begin
                    ContainerName := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J), DEFAULT);
                    ContainerAction := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J) + '_Type', DEFAULT);

                    // Add valid containers to the list
                    if (ContainerName <> DEFAULT) then
                    begin
                        ContainerProps := TStringList.Create;
                        try
                            // Add container properties
                            AddJSONProperty(ContainerProps, 'container_name', ContainerName);
                            AddJSONProperty(ContainerProps, 'container_type', ContainerAction);
                            //AddJSONProperty(ContainerProps, 'group', IntToStr(G));
                            //AddJSONProperty(ContainerProps, 'container_id', IntToStr(J));

                            // Add to containers array
                            ContainersArray.Add(BuildJSONObject(ContainerProps, 1));
                        finally
                            ContainerProps.Free;
                        end;
                    end;

                    Inc(J);
                    // Exit if we've reached the default value
                    if ContainerName = DEFAULT then
                        break;
                End;

                Inc(G);
                // Exit if we've reached the default value after first group
                if (G > 1) and (ContainerName = DEFAULT) then
                    break;
            End;
        finally
            IniFile.Free;
        end;

        // Add success status and containers array to result
        AddJSONBoolean(ResultProps, 'success', True);
        ResultProps.Add(BuildJSONArray(ContainersArray, 'containers'));

        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR + 'temp_outjob_containers.json');
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        ContainersArray.Free;
    end;
end;

// Helper function to check if a document is open
function IsOpenDoc(Path: String): Boolean;
var
    Project     : IProject;
    ProjectIdx, I  : Integer;
    Doc: IDocument;
begin
    result := False;

    for ProjectIdx := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Project := GetWorkspace.DM_Projects(ProjectIdx);
        if Project = Nil then Exit;

        if Path = Project.DM_ProjectFullPath then
        begin
            result := True;
            Exit;
        end;

        // Iterate documents
        for I := 0 to Project.DM_LogicalDocumentCount - 1 do
        begin
            Doc := Project.DM_LogicalDocuments(I);
            if Path = Doc.DM_FullPath then
            begin
                result := True;
                Exit;
            end;
        end;
    end;
end;

// A file path in one canonical spelling for comparisons: lower case,
// backslashes, and doubled separators collapsed (a leading \\ of a UNC path
// is kept)
function NormalizedPath(Path: String): String;
var
    Lead : String;
begin
    Result := LowerCase(StringReplace(Trim(Path), '/', '\', REPLACEALL));
    Lead := '';
    if (Copy(Result, 1, 2) = '\\') then
    begin
        Lead := '\\';
        Result := Copy(Result, 3, Length(Result));
    end;
    while (Pos('\\', Result) > 0) do
        Result := StringReplace(Result, '\\', '\', REPLACEALL);
    Result := Lead + Result;
end;

// The open document for a file, or nil. Paths are compared case-
// insensitively with separators normalised: Client.IsDocumentOpen misses a
// path spelled differently, and the file would be opened a second time.
function FindOpenDocument(Path: String): IServerDocument;
var
    i, j   : Integer;
    Module : IServerModule;
    Want   : String;
begin
    Result := nil;
    Want := NormalizedPath(Path);
    for i := 0 to Client.Count - 1 do
    begin
        Module := Client.ServerModule[i];
        if (Module <> nil) then
            for j := 0 to Module.DocumentCount - 1 do
                if (NormalizedPath(Module.Documents[j].FileName) = Want) then
                begin
                    Result := Module.Documents[j];
                    Exit;
                end;
    end;
end;

// Focus a document, opening it only when it is not open yet: re-opening an
// open document reloads it from disk and silently discards unsaved changes.
// Returns nil when it cannot be opened. A library ('SchLib' or 'PcbLib')
// opened here goes on OpenedLibraries, and the request closes it before it
// answers; one that was open before the call never does, so a library the
// operator has open stays open.
function OpenLibraryDocument(Kind: String; Path: String): IServerDocument;
begin
    Result := FindOpenDocument(Path);
    if (Result = nil) then
    begin
        Result := Client.OpenDocument(Kind, Path);
        if (Result <> nil) and ((UpperCase(Kind) = 'SCHLIB') or (UpperCase(Kind) = 'PCBLIB')) then
            OpenedLibraries.Add(Path);
    end;
    if (Result <> nil) then
    begin
        Client.ShowDocument(Result);
        Sleep(500);
    end;
end;

// Close the libraries the running request opened, each by the path it was
// opened with, saving nothing: a library read to copy a symbol or to list
// its primitives leaves the operator's workspace with the call (a house
// library left open raises Altium's reload question when the factory's
// build rewrites it). Each leaves the list as it is closed, so a second call
// closes only what is left.
procedure CloseOpenedLibraries;
var
    Doc : IServerDocument;
begin
    while (OpenedLibraries.Count > 0) do
    begin
        Doc := Client.GetDocumentByPath(OpenedLibraries[0]);
        OpenedLibraries.Delete(0);
        if (Doc <> nil) then
        begin
            Doc.Modified := False;
            Client.CloseDocument(Doc);
        end;
    end;
end;

// Run output job containers of an OutJob: OutJobPath, else the focused or first open one. The OutJob is shown
// first and no output base path is passed, as in the two scripts that generated LED240's and Elch's JLC sets;
// the tool had reported success with nothing written on Elch's TEMPMODUL (2026-10-23). "ran" says the process
// was started; the server lists the files each container wrote.
function RunOutputJobs(ContainerNames: TStringList, ROOT_DIR: String, OutJobPath: String): String;
var
    IniFile: TIniFile;
    ContainerName, ContainerAction, RelativePath: String;
    G, J, I: Integer;
    S: String;
    ResultProps: TStringList;
    ContainerResults: TStringList;
    ContainerResultProps: TStringList;
    ContainerFound: Boolean;
    OutJobDoc: IServerDocument;
    OutputLines: TStringList;
begin
    if (OutJobPath = '') then OutJobPath := GetOpenOutputJob();
    if (OutJobPath = '') then
    begin
        Result := '{"success": false, "error": "No open OutJob document found"}';
        Exit;
    end;

    // Found open first: opening an open document again reloads it and drops its unsaved changes
    OutJobDoc := OpenLibraryDocument('OUTPUTJOB', OutJobPath);
    if (OutJobDoc = Nil) then
    begin
        Result := '{"success": false, "error": "' + JSONEscapeString('Could not open OutJob document: ' + OutJobPath) + '"}';
        Exit;
    end;
    OutJobDoc.Focus;

    ResultProps := TStringList.Create;
    ContainerResults := TStringList.Create;
    try
        AddJSONProperty(ResultProps, 'outjob_path', OutJobPath);

        // The OutJob file is an INI file
        IniFile := TIniFile.Create(OutJobPath);
        try
            for I := 0 to ContainerNames.Count - 1 do
            begin
                ContainerFound := False;
                ContainerResultProps := TStringList.Create;
                try
                    AddJSONProperty(ContainerResultProps, 'container_name', ContainerNames[I]);
                    G := 1;
                    while True do
                    begin
                        S := 'OutputGroup' + IntToStr(G);
                        J := 1;
                        while True do
                        begin
                            ContainerName := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J), DEFAULT);
                            if ContainerName = DEFAULT then
                                break;
                            if ContainerName = ContainerNames[I] then
                            begin
                                ContainerFound := True;
                                BridgeProgress('container ' + ContainerName, I + 1, ContainerNames.Count);
                                ContainerAction := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J) + '_Type', DEFAULT);
                                OutJobDoc.Focus;
                                if ContainerAction = 'GeneratedFiles' then
                                begin
                                    ResetParameters;
                                    AddStringParameter('Action', 'Run');
                                    AddStringParameter('OutputMedium', ContainerName);
                                    AddStringParameter('ObjectKind', 'OutputBatch');
                                    RunProcess('WorkspaceManager:GenerateReport');
                                    AddJSONBoolean(ContainerResultProps, 'ran', True);
                                end
                                else if ContainerAction = 'Publish' then
                                begin
                                    RelativePath := IniFile.ReadString('PublishSettings', 'OutputBasePath' + IntToStr(J), '');
                                    ResetParameters;
                                    AddStringParameter('Action', 'PublishToPDF');
                                    AddStringParameter('OutputMedium', ContainerName);
                                    AddStringParameter('ObjectKind', 'OutputBatch');
                                    AddStringParameter('OutputBasePath', RelativePath);
                                    AddStringParameter('DisableDialog', 'True');
                                    RunProcess('WorkspaceManager:Print');
                                    AddJSONBoolean(ContainerResultProps, 'ran', True);
                                end
                                else
                                begin
                                    AddJSONBoolean(ContainerResultProps, 'ran', False);
                                    AddJSONProperty(ContainerResultProps, 'error', 'Unknown container action type: ' + ContainerAction);
                                end;
                                break;
                            end;
                            Inc(J);
                        end;
                        if ContainerFound then
                            break;
                        if ContainerName = DEFAULT then
                            break;
                        Inc(G);
                    end;
                    if not ContainerFound then
                    begin
                        AddJSONBoolean(ContainerResultProps, 'ran', False);
                        AddJSONProperty(ContainerResultProps, 'error', 'Container not found: ' + ContainerNames[I]);
                    end;
                    ContainerResults.Add(BuildJSONObject(ContainerResultProps, 1));
                finally
                    ContainerResultProps.Free;
                end;
            end;
        finally
            IniFile.Free;
        end;

        AddJSONBoolean(ResultProps, 'success', True);
        ResultProps.Add(BuildJSONArray(ContainerResults, 'container_results'));
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR + 'temp_run_output_jobs.json');
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        ContainerResults.Free;
    end;
end;

// Mark an open document as changed, so its tab shows it unsaved and Altium
// asks before closing it. Objects a script adds to a library through
// PreProcess/PostProcess do not reliably set the flag (a PCB library never
// does); the user reviews and saves.
procedure MarkDocumentModified(Path: String);
var
    Doc : IServerDocument;
begin
    Doc := FindOpenDocument(Path);
    if (Doc <> nil) then
        Doc.Modified := True;
end;

// ---------------------------------------------------------------------------
// Project-level read-only queries (get_project_info, get_erc_violations).
// Both work on the focused project and never save or modify documents.
// ---------------------------------------------------------------------------

// Error level of an IViolation as text (TErrorLevel).
function ErrorLevelName(Level: Integer): String;
begin
    case Level of
        0: Result := 'no_report';
        1: Result := 'warning';
        2: Result := 'error';
        3: Result := 'fatal';
    else
        Result := 'level_' + IntToStr(Level);
    end;
end;

// Kind of an IComponentVariation as text (TVariationKind).
function VariationKindName(Kind: Integer): String;
begin
    case Kind of
        0: Result := 'none';
        1: Result := 'not_fitted';
        2: Result := 'alternate';
    else
        Result := 'kind_' + IntToStr(Kind);
    end;
end;

// Focused project: parameters, documents (with unsaved-changes flag) and variants
function GetProjectInfo(ROOT_DIR: String): String;
var
    Project      : IProject;
    Doc          : IDocument;
    ServerDoc    : IServerDocument;
    ProjVariant  : IProjectVariant;
    Variation    : IComponentVariation;
    I, J         : Integer;
    SchCount     : Integer;
    ModifiedCount: Integer;
    ResultProps  : TStringList;
    ParamsArray  : TStringList;
    DocsArray    : TStringList;
    VariantsArray: TStringList;
    VariationsArr: TStringList;
    ItemProps    : TStringList;
    OutputLines  : TStringList;
    OpenState    : String;
begin
    Project := GetWorkspace.DM_FocusedProject;
    if Project = Nil then
    begin
        Result := 'ERROR: No focused project';
        Exit;
    end;

    ResultProps := TStringList.Create;
    ParamsArray := TStringList.Create;
    DocsArray := TStringList.Create;
    VariantsArray := TStringList.Create;
    try
        AddJSONProperty(ResultProps, 'project_path', Project.DM_ProjectFullPath);

        for I := 0 to Project.DM_ParameterCount - 1 do
        begin
            ItemProps := TStringList.Create;
            try
                AddJSONProperty(ItemProps, 'name', Project.DM_Parameters(I).DM_Name);
                AddJSONProperty(ItemProps, 'value', Project.DM_Parameters(I).DM_Value);
                ParamsArray.Add(BuildJSONObject(ItemProps, 1));
            finally
                ItemProps.Free;
            end;
        end;

        SchCount := 0;
        ModifiedCount := 0;
        for I := 0 to Project.DM_LogicalDocumentCount - 1 do
        begin
            Doc := Project.DM_LogicalDocuments(I);
            if Doc.DM_DocumentKind = 'SCH' then Inc(SchCount);
            ServerDoc := Client.GetDocumentByPath(Doc.DM_FullPath);
            if ServerDoc = Nil then
                OpenState := 'closed'
            else if ServerDoc.Modified then
            begin
                OpenState := 'modified';
                Inc(ModifiedCount);
            end
            else
                OpenState := 'open';
            ItemProps := TStringList.Create;
            try
                AddJSONProperty(ItemProps, 'path', Doc.DM_FullPath);
                AddJSONProperty(ItemProps, 'kind', Doc.DM_DocumentKind);
                AddJSONProperty(ItemProps, 'state', OpenState);
                DocsArray.Add(BuildJSONObject(ItemProps, 1));
            finally
                ItemProps.Free;
            end;
        end;

        for I := 0 to Project.DM_ProjectVariantCount - 1 do
        begin
            ProjVariant := Project.DM_ProjectVariants(I);
            VariationsArr := TStringList.Create;
            ItemProps := TStringList.Create;
            try
                for J := 0 to ProjVariant.DM_VariationCount - 1 do
                begin
                    Variation := ProjVariant.DM_Variations(J);
                    VariationsArr.Add('{"designator": "' + JSONEscapeString(Variation.DM_PhysicalDesignator) +
                        '", "kind": "' + VariationKindName(Variation.DM_VariationKind) + '"}');
                end;
                AddJSONProperty(ItemProps, 'name', ProjVariant.DM_Description);
                ItemProps.Add(BuildJSONArray(VariationsArr, 'variations', 1));
                VariantsArray.Add(BuildJSONObject(ItemProps, 1));
            finally
                ItemProps.Free;
                VariationsArr.Free;
            end;
        end;

        ResultProps.Add(BuildJSONArray(ParamsArray, 'parameters'));
        AddJSONInteger(ResultProps, 'schematic_sheet_count', SchCount);
        AddJSONInteger(ResultProps, 'modified_document_count', ModifiedCount);
        ResultProps.Add(BuildJSONArray(DocsArray, 'documents'));
        ResultProps.Add(BuildJSONArray(VariantsArray, 'variants'));

        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR + 'temp_project_info.json');
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        ParamsArray.Free;
        DocsArray.Free;
        VariantsArray.Free;
    end;
end;

// Compile the focused project (in memory, nothing is saved) and list the
// compiler/ERC violations. Violations live on the project object; the
// per-document DM_ViolationCount does not exist in recent Altium versions.
function GetERCViolations(ROOT_DIR: String; DoCompile: Boolean): String;
var
    Project     : IProject;
    Violation   : IViolation;
    I, Level    : Integer;
    NFatal, NError, NWarning : Integer;
    ResultProps : TStringList;
    ViolArray   : TStringList;
    ItemProps   : TStringList;
    OutputLines : TStringList;
begin
    Project := GetWorkspace.DM_FocusedProject;
    if Project = Nil then
    begin
        Result := 'ERROR: No focused project';
        Exit;
    end;

    if DoCompile then
        Project.DM_Compile;

    NFatal := 0; NError := 0; NWarning := 0;

    ResultProps := TStringList.Create;
    ViolArray := TStringList.Create;
    try
        for I := 0 to Project.DM_ViolationCount - 1 do
        begin
            Violation := Project.DM_Violations(I);
            Level := Violation.DM_ErrorLevel;
            if Level = 3 then Inc(NFatal);
            if Level = 2 then Inc(NError);
            if Level = 1 then Inc(NWarning);
            ItemProps := TStringList.Create;
            try
                AddJSONProperty(ItemProps, 'level', ErrorLevelName(Level));
                AddJSONProperty(ItemProps, 'type', Violation.DM_DescriptorString);
                AddJSONProperty(ItemProps, 'detail', Violation.DM_DetailString);
                ViolArray.Add(BuildJSONObject(ItemProps, 1));
            finally
                ItemProps.Free;
            end;
        end;

        AddJSONProperty(ResultProps, 'project_path', Project.DM_ProjectFullPath);
        AddJSONBoolean(ResultProps, 'compiled', DoCompile);
        AddJSONInteger(ResultProps, 'total', ViolArray.Count);
        AddJSONInteger(ResultProps, 'fatal', NFatal);
        AddJSONInteger(ResultProps, 'errors', NError);
        AddJSONInteger(ResultProps, 'warnings', NWarning);
        ResultProps.Add(BuildJSONArray(ViolArray, 'violations'));

        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR + 'temp_erc.json');
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        ViolArray.Free;
    end;
end;

// Every component of the compiled (flattened) project with its comment,
// footprint, library reference, sheet and parameters. Compiles first.
function GetProjectComponents(ROOT_DIR: String; IncludeParameters: Boolean): String;
var
    Project     : IProject;
    Flat        : IDocument;
    Comp        : IComponent;
    I, J        : Integer;
    CompArray   : TStringList;
    CompProps   : TStringList;
    ParamArray  : TStringList;
    ResultProps : TStringList;
    OutputLines : TStringList;
begin
    Project := GetWorkspace.DM_FocusedProject;
    if Project = Nil then
    begin
        Result := 'ERROR: No focused project';
        Exit;
    end;
    Project.DM_Compile;
    Flat := Project.DM_DocumentFlattened;
    if Flat = Nil then
    begin
        Result := 'ERROR: Project could not be compiled (no flattened document)';
        Exit;
    end;

    CompArray := TStringList.Create;
    ResultProps := TStringList.Create;
    try
        for I := 0 to Flat.DM_ComponentCount - 1 do
        begin
            Comp := Flat.DM_Components(I);
            CompProps := TStringList.Create;
            ParamArray := TStringList.Create;
            try
                if IncludeParameters then
                    for J := 0 to Comp.DM_ParameterCount - 1 do
                        ParamArray.Add('{"name": "' + JSONEscapeString(Comp.DM_Parameters(J).DM_Name) +
                            '", "value": "' + JSONEscapeString(Comp.DM_Parameters(J).DM_Value) + '"}');
                AddJSONProperty(CompProps, 'designator', Comp.DM_PhysicalDesignator);
                AddJSONProperty(CompProps, 'comment', Comp.DM_Comment);
                AddJSONProperty(CompProps, 'footprint', Comp.DM_Footprint);
                AddJSONProperty(CompProps, 'library_reference', Comp.DM_LibraryReference);
                AddJSONProperty(CompProps, 'sheet', ExtractFileName(Comp.DM_OwnerDocumentFullPath));
                if IncludeParameters then
                    CompProps.Add(BuildJSONArray(ParamArray, 'parameters', 1));
                CompArray.Add(BuildJSONObject(CompProps, 1));
            finally
                CompProps.Free;
                ParamArray.Free;
            end;
        end;

        AddJSONProperty(ResultProps, 'project_path', Project.DM_ProjectFullPath);
        AddJSONInteger(ResultProps, 'component_count', CompArray.Count);
        ResultProps.Add(BuildJSONArray(CompArray, 'components'));
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := BuildJSONObject(ResultProps);
            Result := WriteJSONToFile(OutputLines, ROOT_DIR + 'temp_project_components.json');
        finally
            OutputLines.Free;
        end;
    finally
        CompArray.Free;
        ResultProps.Free;
    end;
end;


// The items of a list joined by commas
function CommaList(L: TStringList): String;
var
    i : Integer;
begin
    Result := '';
    for i := 0 to L.Count - 1 do
    begin
        if (i > 0) then Result := Result + ',';
        Result := Result + L[i];
    end;
end;

// The open project that lists a document, or nil
function DocumentProject(Path: String): IProject;
var
    i, j : Integer;
    Prj  : IProject;
begin
    Result := nil;
    for i := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Prj := GetWorkspace.DM_Projects(i);
        if (Prj <> nil) then
            for j := 0 to Prj.DM_LogicalDocumentCount - 1 do
                if (NormalizedPath(Prj.DM_LogicalDocuments(j).DM_FullPath) = NormalizedPath(Path)) then
                begin
                    Result := Prj;
                    Exit;
                end;
    end;
end;

// The name a PcbPrintLayer record gives a copper layer by its V6 id; the OutJob writes layer names without spaces
function PrintCopperName(V6: Integer): String;
begin
    Result := '';
    if (V6 = 1) then Result := 'TopLayer'
    else if (V6 >= 2) and (V6 <= 31) then Result := 'MidLayer' + IntToStr(V6 - 1)
    else if (V6 = 32) then Result := 'BottomLayer'
    else if (V6 >= 39) and (V6 <= 54) then Result := 'InternalPlane' + IntToStr(V6 - 38);
end;

// The print records of the board's displayed layers, top first: Altium prints the first record over the rest (the
// order flipped which pour covered the other, scratch board 2026-10-23), so the top side is drawn over the bottom
// as the view does from above, its silk and the mechanical outlines over the copper; Names gets the layer names
function PrintLayerRecords(Board: IPCB_Board; Names: TStringList): String;
var
    LS     : IPCB_LayerStack_V7;
    LO     : IPCB_LayerObject;
    ML     : IPCB_MechanicalLayer;
    i      : Integer;
begin
    Names.Clear;
    LS := Board.LayerStack_V7;
    if Board.LayerIsDisplayed[eKeepOutLayer] then Names.Add('KeepOutLayer');
    if Board.LayerIsDisplayed[eTopOverlay] then Names.Add('TopOverlay');
    for i := 1 to 32 do
    begin
        ML := LS.LayerObject_V7[ILayer.MechanicalLayer(i)];
        if ML.MechanicalLayerEnabled and ML.IsDisplayed[Board] then Names.Add('Mechanical' + IntToStr(i));
    end;
    if Board.LayerIsDisplayed[eMultiLayer] then Names.Add('MultiLayer');
    LO := LS.FirstLayer;
    while (LO <> nil) do
    begin
        if LO.IsDisplayed[Board] and (PrintCopperName(LO.V6_LayerID) <> '') then
            Names.Add(PrintCopperName(LO.V6_LayerID));
        LO := LS.NextLayer(LO);
    end;
    if Board.LayerIsDisplayed[eBottomOverlay] then Names.Add('BottomOverlay');
    Result := '';
    for i := 0 to Names.Count - 1 do
    begin
        if (i > 0) then Result := Result + #13#10;
        Result := Result + 'Configuration1_Name' + IntToStr(i + 3) + '=OutputConfigurationParameter' + IntToStr(i + 3) +
                  #13#10 + 'Configuration1_Item' + IntToStr(i + 3) + '=' +
                  StringReplace(PRINT_LAYER_RECORD, '%L%', Names[i], REPLACEALL);
    end;
end;

// The print area: the design extent, or the bounding box of the components named in DesignatorsList with a margin
// of a tenth of the larger span (at least 100 mil), in Altium's internal units as the OutJob takes them. Found gets
// how many were on the board, Missing the others.
function PrintArea(Board: IPCB_Board; DesignatorsList: TStringList; Missing: TStringList; var Found: Integer): String;
var
    Component  : IPCB_Component;
    Rect       : TCoordRect;
    i          : Integer;
    MinX, MinY, MaxX, MaxY, Margin : Integer;
begin
    Found := 0;
    Result := 'PrintArea=DesignExtent|PrintAreaLowerLeftCornerX=0|PrintAreaLowerLeftCornerY=0|' +
              'PrintAreaUpperRightCornerX=0|PrintAreaUpperRightCornerY=0';
    if (DesignatorsList = nil) then Exit;
    for i := 0 to DesignatorsList.Count - 1 do
    begin
        Component := Board.GetPcbComponentByRefDes(Trim(DesignatorsList[i]));
        if (Component = nil) then
            Missing.Add(Trim(DesignatorsList[i]))
        else
        begin
            Rect := Component.BoundingRectangleNoNameComment;
            if (Found = 0) then
            begin
                MinX := Rect.Left;
                MaxX := Rect.Right;
                MinY := Rect.Bottom;
                MaxY := Rect.Top;
            end
            else
            begin
                if Rect.Left < MinX then MinX := Rect.Left;
                if Rect.Right > MaxX then MaxX := Rect.Right;
                if Rect.Bottom < MinY then MinY := Rect.Bottom;
                if Rect.Top > MaxY then MaxY := Rect.Top;
            end;
            Found := Found + 1;
        end;
    end;
    if (Found = 0) then Exit;
    Margin := MaxX - MinX;
    if (MaxY - MinY) > Margin then Margin := MaxY - MinY;
    Margin := Margin div 10;
    if Margin < MilsToCoord(100) then Margin := MilsToCoord(100);
    Result := 'PrintArea=SpecificArea|PrintAreaLowerLeftCornerX=' + IntToStr(MinX - Margin) +
              '|PrintAreaLowerLeftCornerY=' + IntToStr(MinY - Margin) +
              '|PrintAreaUpperRightCornerX=' + IntToStr(MaxX + Margin) +
              '|PrintAreaUpperRightCornerY=' + IntToStr(MaxY + Margin);
end;

// Print a PCB or schematic document to ROOT_DIR\shot.pdf. The OutJob resolves its document only inside the
// document's project (one outside it answers "Missing file", 2026-10-23), so it joins that project for the print:
// added, published with its dialog off, closed and taken out again, the project's modified flag put back and the
// document that had the focus focused again. Nothing is saved.
function PrintView(ViewType: String; DesignatorsList: TStringList; Document: String): String;
var
    Kind, Path, Rel, PrjDir, Area, Layers, Text, Focused, JobPath, Pdf : String;
    Board    : IPCB_Board;
    SchDoc   : ISch_Document;
    Prj      : IProject;
    JobDoc, PrjDoc, FocusDoc, SrvDoc : IServerDocument;
    Was      : Boolean;
    L, Names, Missing, Props : TStringList;
    Found    : Integer;
begin
    Path := Trim(Document);
    if (Path <> '') then
    begin
        if (LowerCase(ExtractFileExt(Path)) = '.pcbdoc') then Kind := 'pcb'
        else if (LowerCase(ExtractFileExt(Path)) = '.schdoc') then Kind := 'sch'
        else
        begin
            Result := 'ERROR: document must be a .PcbDoc or a .SchDoc: ' + Path;
            Exit;
        end;
        SrvDoc := FindOpenDocument(Path);
        if (SrvDoc = nil) then SrvDoc := Client.OpenDocument(UpperCase(Kind), Path);
        if (SrvDoc = nil) then
        begin
            Result := 'ERROR: cannot open ' + Path;
            Exit;
        end;
    end
    else
    begin
        Kind := ViewType;
        if (Kind = 'pcb') then
        begin
            Board := GetBoardSafe(0);
            if (Board <> nil) then Path := Board.FileName;
        end
        else if (Kind = 'sch') then
        begin
            SchDoc := SchServer.GetCurrentSchDocument;
            if (SchDoc <> nil) then Path := SchDoc.DocumentName;
        end;
        if (Path = '') then
        begin
            Result := 'ERROR: no ' + UpperCase(Kind) + ' document is focused';
            Exit;
        end;
    end;

    Prj := DocumentProject(Path);
    if (Prj = nil) then
    begin
        Result := 'ERROR: ' + Path + ' is in no open project; its print needs the project (open_project)';
        Exit;
    end;
    PrjDir := ExtractFilePath(Prj.DM_ProjectFullPath);
    if (PrjDir = '') then
    begin
        // a free document: an OutJob resolves no document outside a project, and joining one publishes nothing
        Result := 'ERROR: ' + Path + ' is a free document (in no project), and Altium prints a document only ' +
                  'inside its project: add it to one, or open it from its project';
        Exit;
    end;
    Rel := Path;
    if (Pos(NormalizedPath(PrjDir), NormalizedPath(Path)) = 1) then
        Rel := Copy(Path, Length(PrjDir) + 1, Length(Path));

    Names := TStringList.Create;
    Missing := TStringList.Create;
    Found := 0;
    Area := '';
    Layers := '';
    if (Kind = 'pcb') then
    begin
        Board := PCBServer.GetPCBBoardByPath(Path);
        if (Board = nil) then
        begin
            Result := 'ERROR: ' + Path + ' is not open as a board';
            Exit;
        end;
        Area := PrintArea(Board, DesignatorsList, Missing, Found);
        Layers := PrintLayerRecords(Board, Names);
    end;

    L := TStringList.Create;
    L.LoadFromFile(ROOT_DIR + 'shot_template.OutJob');
    Text := L.Text;
    Text := StringReplace(Text, '{DIR}', ROOT_DIR, REPLACEALL);
    Text := StringReplace(Text, '{DOC}', Rel, REPLACEALL);
    Text := StringReplace(Text, '{AREA}', Area, REPLACEALL);
    Text := StringReplace(Text, '{LAYERS}', Layers, REPLACEALL);
    L.Text := Text;
    JobPath := ROOT_DIR + 'shot.OutJob';
    L.SaveToFile(JobPath);
    L.Free;
    Pdf := ROOT_DIR + 'shot.pdf';

    Focused := '';
    if (GetWorkspace.DM_FocusedDocument <> nil) then Focused := GetWorkspace.DM_FocusedDocument.DM_FullPath;
    PrjDoc := nil;
    Was := False;
    PrjDoc := Client.GetDocumentByPath(Prj.DM_ProjectFullPath);
    if (PrjDoc <> nil) then Was := PrjDoc.Modified;
    Prj.DM_AddSourceDocument(JobPath);
    BridgeProgress('print ' + Kind, 1, 3);
    JobDoc := Client.OpenDocument('OUTPUTJOB', JobPath);
    if (JobDoc <> nil) then
    begin
        Client.ShowDocument(JobDoc);
        BridgeProgress('publish', 2, 3);
        ResetParameters;
        AddStringParameter('Action', 'PublishToPDF');
        AddStringParameter('ObjectKind', 'OutputBatch');
        AddStringParameter('OutputMedium', 'PDF');
        AddStringParameter('DisableDialog', 'True');
        RunProcess('WorkspaceManager:Print');
        Client.CloseDocument(JobDoc);
    end;
    Prj.DM_RemoveSourceDocument(JobPath);
    if (PrjDoc <> nil) then PrjDoc.Modified := Was;
    if (Focused <> '') then
    begin
        FocusDoc := Client.GetDocumentByPath(Focused);
        if (FocusDoc <> nil) then Client.ShowDocument(FocusDoc);
    end;
    BridgeProgress('printed', 3, 3);

    if (JobDoc = nil) then
        Result := 'ERROR: Altium did not open the print OutJob ' + JobPath
    else if not FileExists(Pdf) then
        Result := 'ERROR: Altium published no PDF of ' + Path
    else
    begin
        Props := TStringList.Create;
        AddJSONBoolean(Props, 'success', True);
        AddJSONProperty(Props, 'view_type', Kind);
        AddJSONProperty(Props, 'document', Path);
        AddJSONProperty(Props, 'project', Prj.DM_ProjectFullPath);
        AddJSONProperty(Props, 'pdf', Pdf);
        AddJSONInteger(Props, 'zoomed_component_count', Found);
        AddJSONProperty(Props, 'missing_designators', CommaList(Missing));
        AddJSONProperty(Props, 'layers', CommaList(Names));
        Result := BuildJSONObject(Props);
        Props.Free;
    end;
    Names.Free;
    Missing.Free;
end;

// get_screenshot: the document printed through Altium's own PCB or Schematic Print, published to PDF in the
// request's folder (shot.pdf), which the server rasterises. A server started before the print path sends no
// OutJob template and gets the window-grab protocol (TakeViewFocus).
function TakeViewScreenshot(ViewType: String; DesignatorsList: TStringList; Document: String): String;
begin
    if FileExists(ROOT_DIR + 'shot_template.OutJob') then
        Result := PrintView(LowerCase(ViewType), DesignatorsList, Document)
    else
        Result := TakeViewFocus(ViewType, DesignatorsList);
end;
