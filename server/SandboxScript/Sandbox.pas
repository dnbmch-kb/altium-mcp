// Isolated DelphiScript sandbox used by the run_altium_script MCP tool.
//
// This is deliberately a SEPARATE script project from Altium_API: a compile
// error or crash in user-supplied script must never break the working MCP
// tooling.
//
// SandboxLog() flushes to disk on every call, so when a script dies silently
// (Altium leaves it paused in the debugger with no dialog) the log still shows
// the last step that completed - the statement after it is the culprit.

const
    REPLACEALL = 1;

var
    LogLines : TStringList;
    LogPath  : String;
    OutPath  : String;
    // Scratch variables: DelphiScript has no inline declarations, so scripts
    // passed to the tool reuse these rather than declaring their own.
    S1, S2, S3 : String;
    I1, I2, I3 : Integer;
    B1         : Integer;
    Obj1, Obj2, Obj3, Obj4, Obj5 : IDispatch;
    List1      : TStringList;
    IntMan     : IIntegratedLibraryManager;
    DbDoc      : IDatabaseLibDocument;

procedure SandboxLog(Msg: String);
begin
    LogLines.Add(Msg);
    LogLines.SaveToFile(LogPath);
end;

procedure Run;
var
    ResultText : String;
    OutLines   : TStringList;
begin
    LogPath := 'C:\Users\Public\altium_mcp\sandbox_log.txt';
    OutPath := 'C:\Users\Public\altium_mcp\sandbox_result.json';
    LogLines := TStringList.Create;
    ResultText := '{"sandbox": "no result set"}';
    SandboxLog('sandbox start');

    try
        // === BEGIN EXPERIMENT (rewritten by the run_altium_script tool) ===
        SandboxLog('doc');
        Obj1 := SchServer.GetSchDocumentByPath('C:\Projects\ugv\ugv-pcb\PWRBASE\PWRBASE.SchDoc');
        SchServer.ProcessControl.PreProcess(Obj1, '');
        SandboxLog('new marker at U20.6 end, Altium default');
        Obj4 := SchServer.SchObjectFactory(eNoERC, eCreate_Default);
        Obj4.Location := Point(MilsToCoord(38750), MilsToCoord(2400));
        Obj1.RegisterSchObjectInContainer(Obj4);
        SchServer.RobotManager.SendMessage(Obj1.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Obj4.I_ObjectAddress);
        SandboxLog('registered: active ' + BoolToStr(Obj4.IsActive) + ' suppressall ' + BoolToStr(Obj4.SuppressAll) + ' orient ' + IntToStr(Obj4.Orientation) + ' symbol ' + IntToStr(Obj4.Symbol));
        SchServer.ProcessControl.PostProcess(Obj1, '');
        Obj1.GraphicallyInvalidate;
        ResultText := 'placed a default NoERC at 38750,2400';
        // === END EXPERIMENT ===
    except
        SandboxLog('EXCEPTION escaped the script body');
        ResultText := '{"error": "exception escaped script - see log for last step"}';
    end;

    SandboxLog('sandbox end');

    OutLines := TStringList.Create;
    try
        OutLines.Text := ResultText;
        OutLines.SaveToFile(OutPath);
    finally
        OutLines.Free;
    end;
end;
