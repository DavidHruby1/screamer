# Produktová analýza Screameru (říjen 2026)

> Stav k 2026-10-31 (commit `a81750c`). Analýza vychází z kódu, README a textů v UI.
> Aplikace nebyla spuštěna na Windows. Údaje o konkurenci pocházejí ze stránek výrobců
> a ze srovnávacích článků, které často píší sami konkurenti. Ceny a čísla jsou proto
> orientační. Toto je podklad k rozhodnutí, ne schválený backlog. Ten je v
> [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md#product-requirements-and-acceptance-criteria).

## Shrnutí

Jádro je dobré a v jednom ohledu lepší než u konkurence: když se během zpracování
změní okno, Screamer text nenapíše jinam a ponechá ho v Recovery. Kolem jádra ale
narostla vrstva obranné složitosti, která pro osobní nástroj nemá odpovídající hodnotu:

| Část | Rozsah |
| --- | --- |
| `src/` | ~8 350 řádků |
| `tests/` | ~5 670 řádků |
| `docs/` | ~8 340 řádků |
| `src/settings_dialog.py` | 2 239 řádků |

Uživatel tu složitost vidí hlavně ve třech místech: **Profiles**, **Recovery** a
**právnicky formulované nápovědy**.

Naopak chybí pár drobností, které mají všichni konkurenti a které výrazně zlepšují
každodenní ovládání:

- Esc pro zrušení nahrávky
- zvuková odezva
- „vlož poslední přepis“ jednou zkratkou

---

## 1. Co je zbytečně složité

### 🔴 Velká úspora

#### 1.1 Profily a aplikační mapování

Místo: `src/settings_dialog.py` (`_build_profiles_tab`), `src/config.py` (rewrite catalog).

- Tři komba za sebou: *Selection mode*, *Default profile* a *Manual profile*. Pak
  *Profile to edit*, ale samotný prompt se edituje **na jiné záložce** (tlačítko
  „Edit selected prompt on LLM tab“).
- Mapování na aplikaci se zadává **ručním psaním celé cesty k .exe**. Po aktualizaci
  aplikace, která změní cestu (typicky Slack nebo Discord ve verzované složce),
  mapování přestane fungovat.
- Úprava vestavěného profilu Clean potichu vytvoří nový custom profil.
- Kód nese migraci původu promptu (`llm_prompt_origin`: `legacy_saved` /
  `default_v2` / `user_saved`) a duplicitní zdroj pravdy `llm_system_prompt` vedle
  katalogu profilů.

**Návrh:**

- Jeden seznam profilů a editor promptu přímo u profilu.
- Mapovat podle **názvu exe** (`slack.exe`), ne podle celé cesty, a přidat tlačítko
  „Přidat aktuální/běžící aplikaci“.
- Zrušit dvojici Default/Manual: v trayi stačí volba „Auto“ nebo konkrétní profil.
- Po migraci odstranit `llm_prompt_origin` a samostatný `llm_system_prompt`.

#### 1.2 „AI Rewrite“ a profil „Raw“ dělají totéž

Tray má checkbox *AI Rewrite* (vypnuto = Raw) a vedle toho submenu *Rewrite Profile*,
ve kterém je také Raw. To jsou dva ovladače pro jednu věc.

**Návrh:** checkbox zrušit. Raw je prostě jeden z profilů.

#### 1.3 Recovery dialog

Místo: `src/recovery_dialog.py`.

- Má 9 tlačítek: Copy raw, Copy final, Arm raw, Arm final, Rerun cleanup, Delete,
  Clear all, Retry STT, Discard audio.
- Koncept „Arm“ (okno se schová, klikneš do cílové aplikace a stiskneš recovery
  zkratku) je neintuitivní.
- V detailu se zobrazují interní ladicí údaje: `Profile ID`, názvy enumů warningů,
  `events=…; post-key=…; reason=…` a „Cleanup candidate preview (persistence not
  verified)“.

**Návrh:**

- Recovery zkratka má vždy vložit **poslední** výsledek, bez armování. Wispr Flow to
  má jako `Shift+Alt+Z`.
- Dialog přejmenovat na „Historie“ a nechat v něm Kopírovat, Vložit znovu, Znovu
  vyčistit a Smazat.
- *Retry STT* ukazovat jen tehdy, když opravdu selhal přepis.
- Ladicí údaje o pokusu o doručení přesunout do logu.

#### 1.4 Opravné UI pro „poškozený katalog / slovník“

Vlastní varování, tlačítka *Reset/Replace damaged catalog…*, uchovávání poškozené
hodnoty přes nesouvisející uložení a validační větve v `validate_config`. Pro INI
soubor jednoho uživatele je to přehnané.

**Návrh:** poškozenou hodnotu zazálohovat do souboru vedle `settings.ini`, vrátit
výchozí stav a ukázat jednu bublinu.

#### 1.5 Záložní (fallback) provideři

Dvě kompletní sady navíc: STT fallback a LLM fallback, každá s klíčem, URL, modelem,
hlavičkami a u STT ještě s přepínačem slovníku. To je zhruba 10 polí v konfiguraci a
dvě skupiny v UI.

- LLM fallback skoro nemá smysl, protože selhání rewrite už teď znamená, že se napíše
  surový přepis.
- STT fallback zčásti nahrazuje *Retry STT*.

**Návrh:** LLM fallback odstranit, STT fallback zvážit.

### 🟡 Střední úspora

#### 1.6 Texty nápověd psané jako právní disclaimery

Příklady:

- „Device IDs/names do not prove physical identity.“
- „Deletion has no secure-erasure guarantee.“
- „The registered-shortcut conflict check cannot detect other low-level-hook apps.“

Jsou přesné, ale snižují srozumitelnost. Patří do README nebo
[SECURITY.md](SECURITY.md), ne do každé obrazovky.

#### 1.7 Recovery shortcut jako textové pole s kanonickou syntaxí

Uživatel má psát `ctrl+alt+shift+key:0x56`, přitom hlavní hotkey má záznam stiskem
kláves.

**Návrh:** použít stejný `HotkeyCaptureEdit` jako u hlavní zkratky.

#### 1.8 Oblíbené jazyky přes ISO kód

Textové pole pro kód a tlačítka Add/Edit/Remove.

**Návrh:** zaškrtávací seznam zhruba 15 běžných jazyků.

#### 1.9 Ruční RMS práh

Číselné pole od 0 do 32767 s hodnotou „Disabled“ plus kalibrace. Běžný uživatel neví,
co s tím.

**Návrh:** kalibrovat automaticky nebo mít rozumný výchozí stav a pole schovat do
pokročilých nastavení.

#### 1.10 Tray menu má 6 submenu

*Hotkey* a *Record Mode* se mění jednou za život a v trayi nemusí být.

**Návrh na tray:**

- Enabled
- Jazyk
- Profil (včetně Raw)
- Vložit/zkopírovat poslední
- Historie
- Nastavení
- Konec

#### 1.11 Custom headers u všech 4 providerů

Je to nika. Stačí je schovat do pokročilých nastavení.

#### 1.12 Nastavení blokuje diktování

Hotkey se ignoruje, dokud je dialog Settings otevřený (`_on_hotkey_pressed` v
`src/main.py`), a tray je zamčený. To brání rychlému cyklu „změním prompt a hned
vyzkouším“.

**Návrh (větší práce):** nemodální nastavení, nebo aspoň povolit diktování s
posledním uloženým stavem.

### 🟢 Repo a dokumentace

Dokumenty, které už neslouží a jejichž historii má git:

- `docs/SUMMARY.md`: 1 161 řádků o původním projektu OVI
- `docs/PLAN.md` (historický plán)
- `docs/OVERVIEW.md` (historický průzkum)
- `docs/RELEASE_PIPELINE_PLAN.md` (výslovně „superseded“)
- `docs/superpowers/plans/*`: 6 dokončených plánů
- `docs/implementation-plan/*`

Můžou pryč nebo do archivu.

---

## 2. Co je dobré a stojí za zachování

- **Kontrola cílového okna a pozdržení výstupu.** Je to unikátní a opravdu užitečné.
  Konkurence má v issue trackerech přesně tyhle chyby
  ([Handy #502](https://github.com/cjpais/Handy/issues/502),
  [Handy #783](https://github.com/cjpais/Handy/issues/783)).
- **Snapshot konfigurace na začátku nahrávky.** Změna v trayi se projeví až v další
  nahrávce.
- **Rychlé přepínání jazyka v trayi.** Pro češtinu a angličtinu klíčové.
- **Živý ukazatel hlasitosti mikrofonu** se jménem zařízení.
- **Šifrování přes DPAPI, žádný účet, vlastní API klíč, psaní přes `SendInput` bez
  zásahu do schránky.** To jsou reálné výhody proti Wispr Flow (cloud a předplatné).

---

## 3. Konkurence

| Aplikace | Windows | Model | Čím vyniká |
| --- | --- | --- | --- |
| **Wispr Flow** | ✅ | cloud, $12–15/měs | Backtrack („2… vlastně 3“ → „3“), styly pro aplikace, Command Mode, zkratka pro vložení posledního přepisu, Esc ruší |
| **Superwhisper** | ✅ | lokální i cloud, vlastní klíč v Pro | Ořez ticha před odesláním, obnova schránky po vložení, ztlumení hudby při nahrávání, Shift na konci = Enter |
| **Aqua Voice** | ✅ | cloud | Vlastní STT model, kontext z obrazovky, Edit Mode |
| **Willow Voice** | ✅ | cloud + offline režim | Učí se styl psaní, automaticky se učící slovník, snippety |
| **Handy** (open source, ~36k ⭐) | ✅ | lokální (Whisper, Parakeet) | Zdarma, VAD, zvuková odezva, historie |
| **OpenWhispr** (open source) | ✅ | lokální i cloud | 4 zkratky, překlad, režim agenta |
| **Whispering / Epicenter** (open source) | ✅ | vlastní klíč, lokální Parakeet | VAD, push-to-talk, transformace schránky |
| **Spokenly** | ✅ | **vlastní klíč zdarma** | Nahrazování slov, příkazy pro interpunkci |
| **VoiceInk** | ❌ (jen Mac) | lokální | Power Mode podle aplikace **i URL**, ztlumení zvuku při nahrávání |
| **Win+H / Voice Access** | vestavěné | Azure / on-device | Zdarma, chybí vlastní slovník |
| **Dragon Professional** | ✅ | lokální, ~$699 | Bez větší verze od 2023 |

**Pozice Screameru je úzká.** Na Windows nabízí vlastní klíč zdarma i Spokenly,
Whispering a OpenWhispr, a Handy je zdarma s lokálními modely. Obhajitelné odlišení
je spolehlivost doručení textu a nativní, nenáročná aplikace bez účtu. Ne počet
funkcí.

### Opakující se vzory u konkurence

1. **Hold i toggle režim:** standard, Screamer má.
2. **Vložit poslední / historie / retry:** Wispr má dedikovanou zkratku. Screamer má
   historii, ale přes neintuitivní Arm.
3. **Slovník a nahrazování slov:** skoro všichni. Lídři mají automaticky se učící
   slovník (Wispr, Willow). Spokenly, VoiceInk a Superwhisper mají deterministické
   nahrazování.
4. **Snippety / rozbalování textu:** Wispr, Willow, Voibe, VoiceInk.
5. **Styly podle aplikace:** Wispr, VoiceInk (aplikace i URL), Superwhisper, Aqua.
   Screamer to má podle exe.
6. **Kontext z obrazovky:** Aqua, Superwhisper, Wispr. Wispr měl kvůli posílání
   snímků obrazovky do cloudu problém s důvěrou.
7. **Command / Edit mode nad označeným textem:** Wispr (placené), Aqua, VoiceInk.
8. **Lokální modely (Parakeet, whisper.cpp):** Handy, OpenWhispr, Spokenly,
   Whispering. Parakeet je na HN popisován jako téměř okamžitý.
9. **Vkládání přes schránku s obnovou:** většina aplikací. Známé riziko je souběh
   obnovy schránky s vložením.
10. **Ztlumení médií při nahrávání:** Superwhisper, VoiceInk, opakovaně žádané.
11. **Ořez ticha / VAD:** Superwhisper, Handy, Whispering.
12. **Streaming:** Aqua (nejvyšší tarif), Willow. U nástrojů s vlastním klíčem vzácné.
13. **Zvuky, Esc pro zrušení, Enter po vložení:** Handy, Wispr, Superwhisper.
14. **Backtrack a odstranění výplňových slov:** Wispr, Monologue, Windows Fluid
    Dictation. Řeší se promptem.

---

## 4. Co převzít, seřazeno podle poměru hodnoty a práce

### Malá práce, velká hodnota

1. **Esc zruší nahrávku.** Teď to jde jen odškrtnutím *Enabled* v trayi. Esc už se v
   hooku zachytává (`_on_kb_event` v `src/hotkey.py`), jen ruší pouze armování.
2. **Zvuky pro start, stop, zrušení a chybu.** Hlavně při Hold režimu je to zásadní
   zpětná vazba.
3. **Zkratka a položka v trayi „Vložit/zkopírovat poslední“.** Nahradí Arm a
   zjednoduší Recovery.
4. **Deterministické nahrazování slov** (např. „pajsajd“ → „PySide6“), aplikované až po
   STT a LLM. Slovník je teď jen nápověda a opakované chyby přepisu neopraví.
5. **Předvolby providerů a tlačítko „Otestovat připojení“.** Předvolby OpenAI, Groq a
   lokální server v jednom rozbalovacím seznamu doplní Base URL a model. Dnes jsou jen
   jako příklady v README.

### Střední práce

6. **Ořez ticha před odesláním.** RMS se už počítá. Ořez sníží latenci, velikost
   uploadu a halucinace Whisperu na tichých úsecích.
7. **Ztlumení nebo pauza médií během nahrávání.** Pozor: přes Core Audio to nejspíš
   znamená novou závislost (`pycaw`), což jde proti zásadě malého projektu.
8. **Volitelný výstup přes schránku s obnovou.** Rychlejší u dlouhých textů a
   potenciálně řeší nahlášený problém s Notepadem. To je hypotéza, neověřená.
   Výchozí chování ať zůstane psaní přes `SendInput`.
9. **Vestavěný profil „Polished“** s backtrackem a odstraněním výplňových slov. Je to
   jen prompt. Clean ať zůstane konzervativní.
10. **Shift při puštění = Enter** místo globálního Post-type key. Rozhodne se pro
    každou zprávu zvlášť, takže celé submenu v trayi odpadne.

### Rozhodnutí na vlastníkovi (v původním backlogu mimo rozsah)

11. **Edit mode nad označeným textem** („přepiš to formálněji“). U konkurence je to
    hlavní placená funkce a technicky se dá postavit na existujícím rewrite. Je ale v
    rozporu s „Outside Product Scope“ ve [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md#product-requirements-and-acceptance-criteria).
12. **Profily podle titulku okna nebo URL v prohlížeči.** Prohlížeč je jeden proces s
    mnoha úlohami, mapování podle exe ho nerozliší.

### Nedoporučeno

- Kontext ze snímků obrazovky (riziko pro soukromí a důvěru).
- Vestavěné lokální modely: stačí podpora lokálního serveru
  ([LOCAL-STT.md](LOCAL-STT.md)).
- Streaming: s Whisper-kompatibilními endpointy je těžký.
- Meetingy, týmové funkce, mobilní aplikace.

---

## 5. Doporučené pořadí pro další iteraci

1. Esc pro zrušení a zvuková odezva.
2. „Vložit poslední“ místo Arm a zjednodušený dialog Historie.
3. Zjednodušení Profiles: mapování podle názvu exe, prompt přímo u profilu.
4. Odstranění checkboxu AI Rewrite a LLM fallbacku.
5. Deterministické nahrazování slov.

---

## 6. Co navíc převzít z Hexu

Zdrojové srovnání s [Hexem, commit `1018aecdbb`](https://github.com/anomalyco/hex/tree/1018aecdbbc82a1dd655964fde800b7319ccf571)
(`dev`, 2026-10-31). Jde o vývojový kód, ne o potvrzení dostupnosti ve vydané verzi;
Hex nebyl v rámci srovnání spuštěn.

Esc, zvuky, vložení posledního přepisu, deterministické opravy, předvolby providerů
a výběr běžící aplikace už jsou doporučené výše. Nové body:

1. **Měřit latenci po jednotlivých krocích.** Zaznamenat dobu od uvolnění zkratky,
   audio tail, STT, rewrite a odeslání vstupu. Porovnávat stejná česká, anglická a
   smíšená technická audia; sledovat medián, p95 a chyby přepisu. Screamer už loguje
   délky některých operací, ale chybí společné vyhodnocení celé cesty. Jeho 250ms
   tail navíc čeká synchronně na Qt vlákně. Nezkracovat jej bez ověření konců slov.
2. **Krátký průvodce prvním diktátem.** Navázat na předvolby a test připojení z bodu
   4.5: vybrat mikrofon, ukázat jeho úroveň a nechat uživatele nadiktovat větu do
   cvičného pole. Úspěšný přepis má být možný bez nastavování LLM.
3. **Lokální rewrite bez povinného API klíče.** Na rozdíl od STT dnes
   `ProviderConfig.is_complete` a `_call_llm` vyžadují neprázdný klíč. Umožnit i
   kompatibilní lokální LLM server bez autentizace; žádná správa modelů navíc.

**Zachovat výhody Screameru:** kontrolu původního cílového okna a uchování raw textu
před rewrite. V prohlédnuté Windows cestě Hexu se historie zapisuje až po úspěšném
odeslání vložení; jeho paster nemá ekvivalent kontroly původního HWND/PID.

**Nepřebírat celý rozsah Hexu:** přepis do Rustu, vlastní UI framework, skriptovací
runtime, meetingy ani správu lokálních modelů. Streamovací model Parakeet Unified
English je pouze anglický; české modely Parakeet v3 a Whisper mají v tomto katalogu
`streams: false`. Nízkou latenci anglického streamingu nelze slibovat pro češtinu.

Podklady v Hexu: [benchmarky](https://github.com/anomalyco/hex/blob/1018aecdbbc82a1dd655964fde800b7319ccf571/src/transcription_benchmark.rs),
[první diktát](https://github.com/anomalyco/hex/blob/1018aecdbbc82a1dd655964fde800b7319ccf571/src/rune_ui/practice.rs),
[modely](https://github.com/anomalyco/hex/blob/1018aecdbbc82a1dd655964fde800b7319ccf571/src/transcription_models.rs),
[Windows pipeline](https://github.com/anomalyco/hex/blob/1018aecdbbc82a1dd655964fde800b7319ccf571/src/windows_dictation.rs),
[Windows paste](https://github.com/anomalyco/hex/blob/1018aecdbbc82a1dd655964fde800b7319ccf571/src/windows_paste.rs).

---

## Zdroje

- Wispr Flow: <https://wisprflow.ai/pricing>, <https://wisprflow.ai/features>,
  <https://docs.wisprflow.ai/articles/2612050838-supported-unsupported-keyboard-hotkey-shortcuts>,
  <https://docs.wisprflow.ai/articles/4816967992-how-to-use-command-mode>
- Superwhisper: <https://superwhisper.com>,
  <https://superwhisper.com/docs/get-started/settings-advanced>,
  <https://feedback.superwhisper.com/board/p/pause-music-when-i-start-to-talk>
- Aqua Voice: <https://aquavoice.com>
- Willow Voice: <https://willowvoice.com>
- Handy: <https://github.com/cjpais/Handy>
- OpenWhispr: <https://docs.openwhispr.com/guides/dictation>,
  <https://openwhispr.com/use-cases/windows>
- Whispering / Epicenter: <https://github.com/EpicenterHQ/epicenter/releases>
- Spokenly: <https://spokenly.app>
- VoiceInk: <https://tryvoiceink.com>, <https://tryvoiceink.com/docs/mode-triggers>,
  <https://tryvoiceink.com/docs/recording-feedback>
- Monologue: <https://www.monologue.to>
- Windows Voice Typing:
  <https://support.microsoft.com/en-us/windows/use-voice-typing-to-talk-instead-of-type-on-your-pc-fec94565-c4bd-329d-e59a-af033fa5689f>
- Srovnávací články (psané konkurenty, brát s rezervou):
  <https://www.getvoibe.com/resources/best-ai-dictation-apps-windows/>,
  <https://www.getvoibe.com/resources/dragon-anywhere-discontinued/>
- Riziko obnovy schránky: <https://docs.rs/win-text-inject>,
  <https://blabbyai.featurebase.app/p/there-is-a-quite-annoying-bug-with-the-restore-clipboard>
