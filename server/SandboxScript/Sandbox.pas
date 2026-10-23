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
        Obj1 := GetWorkspace.DM_GetProjectFromPath('C:\Projects\laser-calib\hw\raw_head_adapter\raw_head_adapter.PrjPcb');
        SandboxLog('project ' + Obj1.DM_ProjectFullPath);
        List1 := TStringList.Create;
        For I1 := 0 To Obj1.DM_LogicalDocumentCount - 1 Do
        Begin
          S1 := Obj1.DM_LogicalDocuments(I1).DM_FullPath;
          If Pos('TEMPMODUL', UpperCase(S1)) > 0 Then List1.Add(S1);
        End;
        SandboxLog('found ' + IntToStr(List1.Count));
        For I1 := 0 To List1.Count - 1 Do
        Begin
          Obj1.DM_RemoveSourceDocument(List1[I1]);
          SandboxLog('removed ' + List1[I1]);
        End;
        S2 := '';
        For I1 := 0 To Obj1.DM_LogicalDocumentCount - 1 Do S2 := S2 + ExtractFileName(Obj1.DM_LogicalDocuments(I1).DM_FullPath) + '; ';
        ResultText := 'removed ' + IntToStr(List1.Count) + '; now: ' + S2;
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
