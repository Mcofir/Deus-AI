; ============================================================
;  installer.iss - סקריפט Inno Setup לבניית מתקין אמיתי ל-Deus
;  (במקום להעביר לאנשים סתם קובץ exe בודד).
;
;  דורש התקנה חד-פעמית של Inno Setup (חינמי):
;      https://jrsoftware.org/isdl.php
;
;  ה-build_exe.bat כבר קורא לקובץ הזה אוטומטית (עם ISCC.exe) אחרי
;  שהוא בונה את dist\Deus.exe עם PyInstaller - אין צורך להריץ ידנית,
;  אלא אם רוצים לקמפל רק את המתקין בלי לבנות מחדש את ה-exe.
; ============================================================

#define MyAppName "Deus"
#define MyAppVersion "1.0"
#define MyAppExeName "Deus.exe"

[Setup]
; GUID קבוע לאפליקציה - אל תשנו בין גרסאות, כדי ש-Windows יזהה שדרוגים
; (ולא יתקין כל גרסה כאפליקציה נפרדת).
AppId={{6B2F6C1E-6C9E-4E1B-9B7D-2F2B8B9B6E9A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=DeusSetup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
; UAC פעם אחת בזמן ההתקנה בלבד (התקנה ל-Program Files) - חוויה רגילה
; ומוכרת לאנשים שהתקינו פעם תוכנה על Windows.
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "צור קיצור בשולחן העבודה"; GroupDescription: "קיצורים נוספים:"

[Files]
; ה-exe עצמו כבר מכיל בתוכו (בזכות --onefile --add-data) את כל
; ה-assets ואת config.json ברירת המחדל - אין צורך להעתיק אותם שוב כאן.
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\הסר התקנה של {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; runasoriginaluser חיוני כאן: PrivilegesRequired=admin למעלה אומר
; שהמתקין עצמו רץ מורם (UAC) - ובלי הדגל הזה, ה-Run הזה (שקורה מיד
; אחרי סיום ההתקנה) היה מפעיל את Deus.exe עם אותו טוקן מורם, לא עם
; ההרשאות הרגילות של המשתמש. זו הייתה הסיבה לבאג אמיתי: ההרצה
; הראשונה (המורמת) של דאוס מורידה את מודל ה-Whisper ויוצרת סימלינק
; אליו במטמון - וסימלינק שנוצר ע"י תהליך מורם הופך ל-"untrusted mount
; point" (WinError 448) עבור כל הרצה *רגילה* (לא מורמת) של דאוס אחרי
; זה, כלומר כל שימוש נורמלי מהתפריט/מהשולחן - התמלול נשבר לצמיתות עד
; שמנקים את המטמון ידנית. runasoriginaluser מפעיל את Deus.exe בהרשאות
; המשתמש הרגילות (לא מורמות) גם כשהמתקין עצמו רץ מורם - בדיוק כמו כל
; הרצה רגילה אחרת שלו, כדי שהסימלינק ייווצר עם ההרשאות הנכונות מההתחלה.
Filename: "{app}\{#MyAppExeName}"; Description: "הפעל את {#MyAppName} עכשיו"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
; מוחק גם את הגדרות המשתמש (config.json) בהסרת ההתקנה - אם רוצים
; לשמר אותן בין התקנות מחדש, פשוט למחוק את השורה הזו.
Type: filesandordirs; Name: "{localappdata}\Deus"

[Code]
// ==========================================================
// מחיקה אופציונלית של מודלי ה-AI שהורדו (מטמון Hugging Face)
// ==========================================================
// דאוס מוריד מודלי תמלול לתיקיית המטמון *המשותפת* הרגילה של
// Hugging Face (%USERPROFILE%\.cache\huggingface\hub) - **לא**
// לתיקיית ההתקנה עצמה, ולכן [UninstallDelete] למעלה (שמוחק רק את
// {localappdata}\Deus) לא נוגע בו בכלל.
//
// חשוב מאוד: מוחקים כאן **אך ורק** את שלוש תיקיות המודלים
// הספציפיות שדאוס עצמו עשוי להוריד (ראו MODEL_FOLDERS למטה) - לפי
// שם מדויק, **לא** את כל תיקיית ה-hub. תיקיית ה-hub היא מטמון
// *משותף* לכל כלי ה-AI במחשב (למשל כלים אחרים שמשתמשים ב-Hugging
// Face) - מחיקה גורפת שלה הייתה מוחקת בטעות גם מודלים שאין לדאוס
// שום קשר אליהם, ולא רק את מה שדאוס עצמו הוריד.
//
// בכוונה **לא** מוחקים את זה אוטומטית תמיד, משתי סיבות נוספות:
//   1. ייתכן שהמשתמש עוד ישתמש בדאוס עם מודל תמלול בעתיד, וההורדה
//      החוזרת לוקחת זמן (כמה דקות, תלוי באינטרנט).
//   2. מי שרק מסיר התקנה זמנית (למשל לצורך עדכון גרסה) כנראה לא
//      רוצה להוריד הכל מחדש בפעם הבאה.
//
// לכן מוצגת תיבת דו-שיח נפרדת (MsgBox) עם כפתורי כן/לא - **"לא"
// הוא כפתור ברירת המחדל** (MB_DEFBUTTON2), כלומר אם המשתמש פשוט
// לוחץ Enter או Space, המודלים נשארים. רק אם לוחץ "כן" במפורש,
// המודלים נמחקים. התיבה מופיעה *בנוסף* לשאלת האישור הרגילה של
// Windows "האם להסיר את Deus?", והבחירה כאן קובעת **רק** אם למחוק
// גם את המודלים שהורדו, ולא משפיעה על הסרת דאוס עצמו.
var
  DeleteModelsConfirmed: Boolean;

// מחזיר את תיקיית הפרופיל של המשתמש שבאמת מריץ/הסיר את דאוס - **לא**
// באמצעות {%USERPROFILE%} (משתנה סביבה גולמי של התהליך המסיר).
//
// זה היה הבאג: מכיוון ש-PrivilegesRequired=admin, הסרת ההתקנה רצה
// מורמת (UAC). אם המשתמש מעלה הרשאות עם חשבון מנהל *נפרד* מהחשבון
// היומיומי שלו (נפוץ מאוד - למשל חשבון "Administrator" נפרד, או
// הרשאות שמנוהלות ע"י IT) - אז {%USERPROFILE%} מצביע על פרופיל חשבון
// המנהל שאיתו אושר ה-UAC, ולא על הפרופיל של המשתמש שבאמת הריץ את דאוס
// והוריד את המודלים. לכן DirExists לא מוצא שום תיקיית מודל, AnyExists
// יוצא False, ו-InitializeUninstall יוצא (Exit) *לפני* שהוא בכלל
// מציג את שאלת המודלים - בדיוק התסמין שנצפה: תיבת האישור הרגילה של
// Windows ("להסיר את Deus?") מופיעה, אבל שאלת המודלים - לא.
//
// {localappdata} של Inno Setup, לעומת זאת, מבוסס על SHGetFolderPath
// שמצביע נכון על המשתמש המחובר בפועל בשולחן העבודה - גם כשהתהליך
// המסיר עצמו רץ תחת טוקן מורם של חשבון מנהל אחר - ולכן הוא הבחירה
// הנכונה כאן. {localappdata} הוא תמיד "<פרופיל>\AppData\Local", אז
// עולים ממנו שתי תיקיות כדי לקבל את שורש הפרופיל.
function GetRealUserProfile(): String;
begin
  Result := ExtractFileDir(ExtractFileDir(ExpandConstant('{localappdata}')));
end;

// שמות התיקיות המדויקות תחת .cache\huggingface\hub שדאוס עשוי ליצור -
// חייב להישאר מסונכרן עם הגדרות ברירת המחדל בקוד עצמו
// (speech/wake_word.py): whisper_model_size (המודל הרגיל),
// whisper_cpu_fallback_model_size (נפילה חזרה ל-CPU), ו-
// whisper_economy_model_size (מצב חסכון). אם המשתמש שינה את אחת
// מההגדרות האלה בקונפיג למודל אחר - התיקייה של המודל המותאם-אישית
// שלו לא תימחק כאן (רק המודלים בברירת המחדל), כדי לא לנחש/למחוק
// דברים שלא בטוח שדאוס בכלל הוריד.
function GetDeusModelFolders(): TArrayOfString;
begin
  SetArrayLength(Result, 3);
  Result[0] := 'models--ivrit-ai--whisper-large-v3-ct2';  // whisper_model_size
  Result[1] := 'models--Systran--faster-whisper-medium';  // whisper_cpu_fallback_model_size
  Result[2] := 'models--Systran--faster-whisper-small';   // whisper_economy_model_size
end;

// מציג תיבת דו-שיח כן/לא לשאלת מחיקת מודלי ה-AI.
// "לא" הוא כפתור ברירת המחדל (MB_DEFBUTTON2) - כלומר: אם המשתמש
// פשוט לוחץ Enter או Space, המודלים **לא** נמחקים. זו אותה התנהגות
// כמו Checkbox לא מסומן כברירת מחדל.
// (CreateCustomForm לא זמין/לא מתקמפל בגרסת Inno Setup הזו - לכן
// חוזרים ל-MsgBox הפשוט שעובד בוודאות, עם ברירת מחדל "לא").
function AskDeleteModels(HubPath: String): Boolean;
var
  NL: String;
begin
  NL := Chr(13) + Chr(10);  // newline - נמנעים מ-#13#10 ש-ISPP מתייחס אליהם כ-preprocessor directive
  Result := (MsgBox(
    'This is a separate, optional question - it does NOT affect ' +
    'uninstalling Deus itself (that is already being uninstalled).' +
    NL + NL +
    'Deus downloaded Hebrew speech-recognition AI model(s) to your ' +
    'shared Hugging Face cache folder:' + NL +
    HubPath + NL + NL +
    'These can take up several gigabytes of disk space. They are ' +
    'KEPT by default - other AI tools on this computer may share ' +
    'that same cache folder, and you might reinstall Deus later ' +
    'and want to reuse it instead of downloading it again.' +
    NL + NL +
    'Do you also want to permanently delete ONLY the specific ' +
    'model(s) Deus itself downloaded (ivrit / medium / small), ' +
    'to free up disk space now?' + NL +
    '(This cannot be undone.)' + NL + NL +
    'Press [No] to keep them (default). Press [Yes] to delete.',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES);
end;

function InitializeUninstall(): Boolean;
var
  HubPath: String;
  Folders: TArrayOfString;
  I: Integer;
  AnyExists: Boolean;
begin
  Result := True;
  DeleteModelsConfirmed := False;

  HubPath := GetRealUserProfile() + '\.cache\huggingface\hub';
  Folders := GetDeusModelFolders();
  AnyExists := False;
  for I := 0 to GetArrayLength(Folders) - 1 do
    if DirExists(HubPath + '\' + Folders[I]) then
      AnyExists := True;

  if not AnyExists then
    Exit; // לא נמצא אף אחד ממודלי דאוס - אין מה לשאול

  DeleteModelsConfirmed := AskDeleteModels(HubPath);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  HubPath: String;
  Folders: TArrayOfString;
  I: Integer;
begin
  if (CurUninstallStep = usPostUninstall) and DeleteModelsConfirmed then
  begin
    HubPath := GetRealUserProfile() + '\.cache\huggingface\hub';
    Folders := GetDeusModelFolders();
    for I := 0 to GetArrayLength(Folders) - 1 do
      DelTree(HubPath + '\' + Folders[I], True, True, True);
  end;
end;
