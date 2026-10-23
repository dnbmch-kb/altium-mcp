"""get_screenshot's picture of a document: Altium's own print, published to PDF and rasterised.

A grab of Altium's window held the previously shown document's pixels while the tab and title named the requested
one (PrintWindow and BitBlt alike, 2026-10-23), so a board or sheet picture is printed instead: the bridge adds an
OutJob with one PCB Print or Schematic Print to the document's own project for the length of the print (an OutJob
outside the project answers "Missing file"), publishes it to PDF, takes it out again and restores the project's
modified flag; this module writes that OutJob, rasterises the PDF and compares the picture with the previous one.
The window grab stays for Altium's dialogs, which have no print.
"""
import hashlib
import io
import json
import time
from pathlib import Path

from PIL import Image

TEMPLATE = "shot_template.OutJob"     # written here; the bridge fills {DOC}, {AREA} and {LAYERS} into shot.OutJob
PDF = "shot.pdf"
LAST = "last_capture.json"            # the previous capture of any server, beside the mailbox
MAX_WIDTH = 4000

_PAGE = ("Record=PageOptions|CenterHorizontal=True|CenterVertical=True|PrintScale=1.00|XCorrection=1.00|"
         "YCorrection=1.00|PrintKind=0|BorderSize=5000000|LeftOffset=0|BottomOffset=0|Orientation=2|PaperLength=1000|"
         "PaperWidth=1000|Scale=100|PaperSource=7|PrintQuality=-3|MediaType=1|DitherType=10|PrintScaleMode=1|"
         "PaperKind=A4|PaperIndex=9")
_PCB_OUT = ("IncludeBoardCutouts=False|IncludeBottomLayerComponents=True|IncludeMultiLayerComponents=True|"
            "IncludeTopLayerComponents=True|IncludeViewports=True|Index=0|Mirror=False|Name=Multilayer Composite Print|"
            "PadNumberFontSize=14|Record=PcbPrintOut|ShowHoles=False|ShowPadNets=False|ShowPadNumbers=False|"
            "SubstituteFonts=False")
_SCH_VIEW = ('Record=SchPrintView|ShowNoERC=True|ShowParamSet=True|ShowProbe=True|ShowBlanket=True|'
             'NoERCSymbolsToShow="Thin Cross","Thick Cross","Small Cross",Checkbox,Triangle|ShowNote=True|'
             'ShowNoteCollapsed=True|ShowOpenEnds=True|ExpandDesignator=True|ExpandNetLabel=False|ExpandPort=False|'
             'ExpandSheetNum=False|ExpandDocNum=False|PrintArea=0|PrintAreaRect.X1=0|PrintAreaRect.Y1=0|'
             'PrintAreaRect.X2=0|PrintAreaRect.Y2=0')


def outjob(kind: str) -> str:
    """The OutJob text for one print of a 'pcb' or 'sch' document, published to shot.pdf in the request's folder
    with the page fitted to the document. The bridge puts in that folder ({DIR}), the document ({DOC}, relative to
    its project), the print area ({AREA}: the design extent or the zoom_to components) and the displayed layers
    ({LAYERS}, one PcbPrintLayer record each, from Configuration1_Item3 on)."""
    folder = "{DIR}"
    lines = ["[OutputJobFile]", "Version=1.0", "", "[OutputGroup1]", "Name=", "Description=", "TargetOutputMedium=PDF",
             "VariantName=[No Variations]", "VariantScope=0", "CurrentConfigurationName=", "TargetPrinter=Virtual Printer",
             "PrinterOptions=Record=PrinterOptions|Copies=1|Duplex=1|TrueTypeOptions=3|Collate=1|PrintJobKind=1|PrintWhat=1",
             "OutputMedium1=PDF", "OutputMedium1_Type=Publish"]
    if kind == "pcb":
        lines += ["OutputType1=PCB Print", "OutputName1=PCB Prints", "OutputCategory1=Documentation",
                  "OutputDocumentPath1={DOC}", "OutputVariantName1=", "OutputEnabled1=1",
                  "OutputEnabled1_OutputMedium1=1", "OutputDefault1=0", "PageOptions1=" + _PAGE,
                  "Configuration1_Name1=OutputConfigurationParameter1",
                  "Configuration1_Item1=DesignatorDisplayMode=Physical|{AREA}|Record=PcbPrintView",
                  "Configuration1_Name2=OutputConfigurationParameter2", "Configuration1_Item2=" + _PCB_OUT,
                  "{LAYERS}", "PcbPrintPreferences1="]
    elif kind == "sch":
        lines += ["OutputType1=Schematic Print", "OutputName1=Schematic Prints", "OutputCategory1=Documentation",
                  "OutputDocumentPath1={DOC}", "OutputVariantName1=[No Variations]", "OutputEnabled1=1",
                  "OutputEnabled1_OutputMedium1=1", "OutputDefault1=0", "PageOptions1=" + _PAGE,
                  "Configuration1_Name1=OutputConfigurationParameter1", "Configuration1_Item1=" + _SCH_VIEW]
    else:
        raise ValueError(f"a print is of a 'pcb' or a 'sch' document, not {kind!r}")
    lines += ["", "[PublishSettings]", f"OutputFilePath1={folder}{PDF}", "ReleaseManaged1=0",
              f"OutputBasePath1={folder}", "OutputPathMedia1=", "OutputPathMediaValue1=", "OutputPathOutputer1=",
              "OutputPathOutputerPrefix1=", "OutputPathOutputerValue1=", f"OutputFileName1={PDF}",
              "OutputFileNameMulti1=", "UseOutputNameForMulti1=0", "OutputFileNameSpecial1=",
              "OpenOutput1=0", "PromptOverwrite1=0", "PublishMethod1=0", "ZoomLevel1=50", "FitSCHPrintSizeToDoc1=1",
              "FitPCBPrintSizeToDoc1=1", "GenerateNetsInfo1=0", "MarkPins1=0", "MarkNetLabels1=0", "MarkPortsId1=0",
              "GenerateTOC1=0", "ShowComponentParameters1=0", "GlobalBookmarks1=0", "PDFACompliance1=Disabled",
              "PDFVersion1=Default", "", "[GeneratedFilesSettings]", f"RelativeOutputPath1={folder}",
              "OpenOutputs1=0", ""]
    return "\r\n".join(lines)


def render_pdf(path, width: int) -> Image.Image:
    """The PDF's first page as RGB, its longer side `width` pixels."""
    import pypdfium2 as pdfium                       # BSD-3-Clause / Apache-2.0, a wheel with PDFium inside
    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[0]
        w, h = page.get_size()
        return page.render(scale=max(1, min(width, MAX_WIDTH)) / max(w, h)).to_pil().convert("RGB")
    finally:
        pdf.close()


def colours(img: Image.Image, most: int = 16) -> int:
    """Distinct colours in the picture, up to most + 1: a print of anything has many, a blank one or two."""
    found = img.getcolors(maxcolors=most)
    return most + 1 if found is None else len(found)


def png(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def fingerprint(img: Image.Image) -> str:
    return hashlib.sha256(f"{img.size}".encode() + img.tobytes()).hexdigest()


def compare_with_last(root, img: Image.Image, what: dict, now: float = None):
    """Record this capture as the last one (shared by every server through `root`) and return the previous capture
    when it is pixel-identical to this one, else None."""
    path = Path(root) / LAST
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    current = dict(what, sha256=fingerprint(img), time=time.time() if now is None else now)
    try:
        staging = path.with_suffix(".tmp")
        staging.write_text(json.dumps(current), encoding="utf-8")
        staging.replace(path)
    except OSError:
        pass
    if previous and previous.get("sha256") == current["sha256"]:
        return previous
    return None


def identical_error(previous: dict) -> str:
    when = time.strftime("%H:%M:%S", time.localtime(previous.get("time", 0)))
    return (f"the capture is pixel-identical to the previous one ({previous.get('kind')} "
            f"{previous.get('document') or previous.get('title') or ''}, {when}): nothing it shows changed since "
            f"then, or the capture did not see the change; the image is attached")
