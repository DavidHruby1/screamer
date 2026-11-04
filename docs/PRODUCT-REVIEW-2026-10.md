# Produktová analýza Screameru (říjen 2026)

> Původní analýza k 2026-10-31 (commit `a81750c`). Body, které už jsou implementované
> (1.1–1.12, úklid repa, 4.1–4.10, 6.1–6.3), byly 2026-11-02 odstraněny; jejich znění
> je v historii gitu. Zůstávají jen otevřené body a kontext. Údaje o konkurenci
> pocházejí ze stránek výrobců a srovnávacích článků, ceny a čísla jsou orientační.
> Ruční ověření implementovaných bodů na Windows zatím neproběhlo.

## Otevřené body

| Bod | Stav |
| --- | --- |
| 3.1 Edit mode | Rozhodnutí vlastníka, mimo rozsah. |
| 3.2 Profily podle URL | Rozhodnutí vlastníka, mimo rozsah. |
| 4 Slovník podle jazyka | Specifikováno, neimplementováno. |
| 5 Oříznuté přepínače | Neopraveno. |

---

## 1. Co je dobré a stojí za zachování

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
- **Uchování raw textu před rewrite** a historie zapsaná i při neúspěšném doručení
  (Hex ji zapisuje až po úspěšném vložení a nekontroluje původní HWND/PID).

---

## 2. Konkurence

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
2. **Vložit poslední / historie / retry:** Wispr má dedikovanou zkratku. Screamer už
   také (Insert last).
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

## 3. Otevřené náměty

### Rozhodnutí na vlastníkovi (v původním backlogu mimo rozsah)

1. **Edit mode nad označeným textem** („přepiš to formálněji“). U konkurence je to
    hlavní placená funkce a technicky se dá postavit na existujícím rewrite. Je ale v
    rozporu s „Outside Product Scope“ ve [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md#product-requirements-and-acceptance-criteria).
2. **Profily podle titulku okna nebo URL v prohlížeči.** Prohlížeč je jeden proces s
    mnoha úlohami, mapování podle exe ho nerozliší.

### Nedoporučeno

- Kontext ze snímků obrazovky (riziko pro soukromí a důvěru).
- Vestavěné lokální modely: stačí podpora lokálního serveru
  ([LOCAL-STT.md](LOCAL-STT.md)).
- Streaming: s Whisper-kompatibilními endpointy je těžký.
- Meetingy, týmové funkce, mobilní aplikace.
- Celý rozsah Hexu: přepis do Rustu, vlastní UI framework, skriptovací runtime,
  meetingy ani správu lokálních modelů. Streamovací Parakeet Unified English je jen
  anglický; české modely mají `streams: false`, nízkou latenci streamingu nelze
  slibovat pro češtinu ([modely v Hexu](https://github.com/anomalyco/hex/blob/1018aecdbbc82a1dd655964fde800b7319ccf571/src/transcription_models.rs)).

---

## 4. Slovník podle jazyka a spolehlivější slovník (2026-11-02)

**Problém:** Termín ve slovníku (např. `PySide6`) se v profilu Raw s výchozím
nastavením nikam neposílá. Rewrite dostává slovník jen mimo Raw a Whisper `prompt`
jen při zapnutém „Send vocabulary to this provider“ (výchozí vypnuto). Nápověda
záložky Vocabulary zmiňuje jen AI rewrite, takže uživatel nepozná, že slovník nic
nedělá. Jeden společný seznam navíc u Whisperu naráží na limit: `prompt` bere jen
posledních ~224 tokenů a zbytek tiše zahodí. Míchání jazyků v promptu může
přepis táhnout do jiného jazyka.

### 4.1 Termíny se štítkem jazyka

- Jeden seznam, ne samostatné slovníky. Každý termín má štítek **Všechny**
  (výchozí) nebo konkrétní jazyk (`cs`, `en`, …, stejné kódy jako výběr jazyka STT).
- Pro diktát se vybere: termíny „Všechny“ + termíny aktuálního jazyka z traye.
- Jazyk **Auto**: posílají se jen termíny „Všechny“.
- Výběr platí stejně pro Whisper `prompt` i pro rewrite.
- UI: tabulka termínů s druhým sloupcem Jazyk (combobox), žádné další záložky.
- Migrace: stávající termíny dostanou „Všechny“; nic se neztratí. Formát
  uložení: JSON objekty `{"term", "language"}` (staré pole řetězců se načte).
- Limity (128 termínů, 128 znaků, 8 000 znaků) zůstávají pro celý seznam.

### 4.2 Whisper prompt

- Místo `Preferred spellings: "PySide6", "Qt"` posílat holý seznam ve tvaru
  předchozího textu: `PySide6, Qt, httpx.` Bez anglické hlavičky a JSON uvozovek.
- Hlídat limit ~224 tokenů: v nastavení ukázat odhad délky vybraného promptu a
  varovat, když se část termínů nevejde. Ořezávat deterministicky (jazykové
  termíny mají přednost před „Všechny“), nikdy potichu.
- Zvážit výchozí zapnutí promptu pro předvolby Groq a OpenAI, které `prompt`
  podporují.

### 4.3 Silnější pokyn pro rewrite

- `VOCABULARY_CONTEXT_PREFIX` výslovně doplnit: slovo, které zní podobně jako
  termín ze slovníku (fonetický chybný přepis, např. „pajsajd šest“), nahradit
  přesným zápisem termínu. Zachovat zákaz vymýšlení obsahu a překladu.

### 4.4 Viditelnost

- Na záložce Vocabulary vysvětlit, kam se slovník posílá (STT prompt, rewrite).
- Varování, když se termíny nikam neposílají (profil Raw a STT prompt vypnutý),
  s odkazem na přepínač a na Replacements jako jedinou deterministickou cestu.

### Ověření

- Unit testy: výběr termínů podle jazyka/Auto, migrace starého formátu, tvar
  Whisper promptu, ořez nad limitem.
- Ruční test na Windows: stejná česká věta s `PySide6` v Raw (s promptem) a v Clean.
- Aktualizovat `docs/IMPLEMENTATION.md` (formát slovníku, chování promptu).

---

## 5. Přepínače oříznuté scrollbarem (2026-11-02)

**Problém:** Přepínače (`ToggleSwitch` v `src/settings_dialog.py`) u pravého okraje
stránky nastavení kolidují se svislým scrollbarem. Jejich pravá část vypadá uříznutá.

**Oprava:** dát přepínačům vpravo odsazení o 2–4 px větší, aby celý přepínač byl
vidět i při zobrazeném scrollbaru. Výsledek ověřit vizuálně na Windows (se
scrollbarem i bez něj, světlé i tmavé téma, různé DPI).

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
