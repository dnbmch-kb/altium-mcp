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
    List1      : TStringList;   // an empty list from Run, which frees it after the script; one the script
                                // creates in its place it frees itself
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
    // The close at the end of the run (Sb: no clash with a script's names)
    SbWsp      : IWorkspace;
    SbPrj      : IProject;
    SbDoc      : IServerDocument;
    SbCopies   : TStringList;
    SbLog      : TStringList;
    SbLogPath, SbOutPath, SbFocused : String;
    SbK        : Integer;
    SbList1    : TStringList;   // the list List1 starts as, freed by Run whatever the script did with List1
begin
    LogPath := 'C:\Users\Public\altium_mcp\sandbox_log.txt';
    OutPath := 'C:\Users\Public\altium_mcp\sandbox_result.json';
    LogLines := TStringList.Create;
    SbList1 := TStringList.Create;
    List1 := SbList1;
    ResultText := '{"sandbox": "no result set"}';
    SandboxLog('sandbox start');

    try
        // === BEGIN EXPERIMENT (rewritten by the run_altium_script tool) ===
        SandboxLog('no script loaded');
        // === END EXPERIMENT ===
    except
        SandboxLog('EXCEPTION escaped the script body');
        ResultText := '{"error": "exception escaped script - see log for last step"}';
    end;

    SandboxLog('sandbox end');
    SbList1.Free;

    // The sandbox's script project leaves the operator's workspace with the run (house rule: after a release it
    // holds nothing an agent opened), and the next run opens it fresh from disk: closed by its path the way
    // tools/altium/altium_run.py's closer closes a staged project - focused with its unit shown, then
    // WorkspaceManager:CloseObject - and the document focused before is shown again. A script runs on after its own
    // project closes (#Notes/guides/altium_scripting.md), so from the first close on only Altium's routines and this
    // procedure's locals: the outcome goes to the step log, then the result is written.
    SbLog := LogLines;
    SbLogPath := LogPath;
    SbOutPath := OutPath;
    SbWsp := GetWorkspace;
    SbFocused := '';
    if (SbWsp.DM_FocusedDocument <> nil) then SbFocused := SbWsp.DM_FocusedDocument.DM_FullPath;
    SbCopies := TStringList.Create;
    for SbK := 0 to SbWsp.DM_ProjectCount - 1 do
        if (Pos('\SERVER\SANDBOXSCRIPT\SANDBOX.PRJSCR', UpperCase(SbWsp.DM_Projects(SbK).DM_ProjectFullPath)) > 0) then
            SbCopies.Add(SbWsp.DM_Projects(SbK).DM_ProjectFullPath);
    for SbK := 0 to SbCopies.Count - 1 do
    begin
        SbPrj := SbWsp.DM_GetProjectFromPath(SbCopies[SbK]);
        if (SbPrj <> nil) then
        begin
            SbPrj.DM_SetAsCurrentProject;
            if (SbPrj.DM_LogicalDocumentCount > 0) then
            begin
                SbDoc := Client.OpenDocument(SbPrj.DM_LogicalDocuments(0).DM_DocumentKind,
                                             SbPrj.DM_LogicalDocuments(0).DM_FullPath);
                if (SbDoc <> nil) then Client.ShowDocument(SbDoc);
            end;
            if (SbWsp.DM_FocusedProject = nil) then
                SbLog.Add('project close refused: ' + SbCopies[SbK] + ': no focused project')
            else if (UpperCase(SbWsp.DM_FocusedProject.DM_ProjectFullPath) <> UpperCase(SbCopies[SbK])) then
                SbLog.Add('project close refused: ' + SbCopies[SbK] + ': the focused project is ' +
                          SbWsp.DM_FocusedProject.DM_ProjectFullPath)
            else
            begin
                ResetParameters;
                AddStringParameter('ObjectKind', 'Project');
                RunProcess('WorkspaceManager:CloseObject');
                if (SbWsp.DM_GetProjectFromPath(SbCopies[SbK]) = nil) then
                    SbLog.Add('project closed: ' + SbCopies[SbK])
                else
                    SbLog.Add('project still open: ' + SbCopies[SbK]);
            end;
        end;
    end;
    SbLog.SaveToFile(SbLogPath);
    if (SbFocused <> '') then
    begin
        SbDoc := Client.GetDocumentByPath(SbFocused);
        if (SbDoc <> nil) then Client.ShowDocument(SbDoc);
    end;
    SbCopies.Free;

    OutLines := TStringList.Create;
    try
        OutLines.Text := ResultText;
        OutLines.SaveToFile(SbOutPath);
    finally
        OutLines.Free;
    end;
end;
