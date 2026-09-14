# Code Style — Klauss Marchi

> How to think about code, for any language and any project. Each section states a principle first;
> the examples only show what that principle looks like in one language. When a situation is not
> covered here, derive the answer from the principle, never from the absence of a rule.
>
> Self-contained: nothing here requires reading another file or running any tool.

---

## 1. Think first, reuse before creating

An AI writes a function several times bigger than needed for three reasons: it treats each request as
a brand-new task, so it duplicates instead of reusing; it wraps everything in error handling nothing
will throw; and it invents abstractions for a second case that never comes. A person who knows the
codebase writes the one obvious path, reuses what is already there, and stops.

Before writing anything, in this order:

1. **Does this already exist here?** A helper, a class, a pattern already in the project. Reusing beats writing.
2. **What is the direct path?** The formulation a reader understands on the first pass.
3. **What did I add that nobody asked for?** A flag, a layer, a parameter with one real value — remove it.
4. **Does it survive reality?** Empty input, absent device, first iteration.

**Small is not the goal — thinking first is.** There is no line budget. A training pipeline, a
protocol, a whole device may need a lot of code, and that is right. Try the direct path first and
grow only when the problem forces it. What separates the two cases is never the size:

| Big because it was not thought through | Big because the problem is big |
|----------------------------------------|--------------------------------|
| dead code — unused import, parameter never read | every piece is reached |
| conditions and loops stacked deep instead of one direct formulation | the depth mirrors the real structure |
| two blocks identical except for constants | each one does a different thing |
| error handling around code that cannot fail | error handling where IO actually fails |
| a knob with a single real value | every knob gets used |

---

## 2. A comment carries what the code cannot say

Code already says *what* it does. A comment exists only for what the code cannot express: the reason
behind a choice, the context that calls it, the physical or protocol constraint. A comment that
restates the signature is noise; a missing comment where the reason is invisible is a loss.

The form is fixed: **one uppercase line, in Portuguese, immediately above every definition** — never
two, never wrapped, never a block.

```python
# CRIA GROUPNORM COM AJUSTE AUTOMATICO DO NUMERO DE GRUPOS (ESTAVEL EM BATCHES PEQUENOS)
def get_norm_3d(num_channels, num_groups=8):

# ❌ FUNCAO QUE RECEBE VALUES E RETORNA A MEDIA          ← repete a assinatura
# ✅ RETORNA A MEDIA DESCARTANDO O MAIOR E O MENOR SOPRO, CHAMADO NO FECHAMENTO DO LOTE
def getMean(self, values):
```

A definition whose name is already the contract — the standard vocabulary of §4 — takes no comment.
Inside a body, comment only a number or formula that cannot explain itself, lowercase and short:
`self.gate_bias = nn.Parameter(torch.tensor(2.0))   # open-gate init (~0.88)`.

**Never**: docstrings, type annotations used as documentation, comment blocks, `=====` banners,
narration of the next line. These are the signature of generated code.

---

## 3. A name belongs to whoever owns the concept

Spelling is not a personal preference — it follows the origin of the concept, so a reader always
knows where a name came from:

| The concept is | Spelling | Examples |
|----------------|----------|----------|
| mine, invented for this project | `camelCase` | `getFiles`, `setFolder`, `handleThread`, `dataDir`, `xData` |
| a library's — a kwarg passed through, an API implemented | keep the library's own spelling | `forward`, `restore_best`, `test_size`, `img_size`, `num_filters` |
| the field's — math, physics, signals | the notation as written on paper | `f_c`, `G_z`, `Ts`, `Xn1`, `mu`, `dt` |

Mixing them in one signature is correct: `GridSearch(model, xData, yData, test_size=0.20)`. Never
translate field notation into prose (`f_c`, not `cutoffFrequency`).

Structural conventions: types and component folders `PascalCase`, constants `UPPER_SNAKE_CASE`,
serialized keys `snake_case`, module files `camelCase` or `index.*`.

**Names are short.** `conc`, `ref`, `ix`, `n`, `cfg`, `msg`, `err`, `res`, `tmp`, `cb`, `fn`, `src`,
`dst` — never `concentrationValue` or `calculatedMeanResult`. Recurring domain data always reuses the
same names, so every file reads the same: `df`, `xData`/`yData`, `xTrain`/`yTest`, `TARGET`, `K_CV`,
`model`, `params`, `history`, `metrics`.

**Never a leading underscore.** Not `_getFeatures`, not `_loadFiles`. Something used once belongs
inline in its caller; something used more than once is a normal public member.

---

## 4. One vocabulary, everywhere

The same verbs in every class and every language, so a reader learns the API once and knows what to
expect from a type never seen before. Never a synonym — no `run`, `compute`, `execute`, `calculate`.

| | |
|---|---|
| `update()` | heavy computation; fills the public attributes |
| `get()` / `set()` / `getData()` | read / write the main value |
| `info()` / `showInfo()` | state summary, as data / printed |
| `plot()` / `show()` / `display()` / `print()` | visual output |
| `process()` | intermediate transform reused inside `update()` |
| `setup()` / `handle()` | lifecycle: one-time init / continuous loop |
| `ready()` / `check()` / `reset()` | predicate / verification with effect / clear state |
| `connect()` / `send()` / `wait()` / `expect()` | IO |
| `start()` / `stop()` / `export()` | long process / cleanup / persist |

Construction only stores what was given; the work happens in `update()`. A constructor that computes
hides cost where nobody expects it.

---

## 5. The shape follows the state

The structure of the code mirrors the structure of the data, not the ambition of the design.

- **State that survives the call, and more than one operation reading it → a type.**
- **No surviving state → a function.** A calculation, a transform, a plot helper.
- **A linear script → linear code.** A simulation or one-off analysis stays top to bottom; wrapping it
  in a class buys nothing.

Signs that something became a type without earning it: it is constructed once and discarded; the
constructor only stores what one method reads; one method does the work and the rest are accessors;
the methods never read what another method wrote; it exists to group helpers, which is what a module
already does.

**Long is not the problem.** When something is long, ask what makes it long. Flat by nature — a
dispatch of sequential conditions, a block of layer declarations, a protocol with many messages — is
correct as it is. Long because two responsibilities merged is a split by responsibility, never by
extracting a private helper that only moves lines around.

Never: an abstract base with one implementation, deep inheritance where composition works, accessors
that only forward a field, an external config file for what fits as data on the type itself.

---

## 6. Difference becomes data

When two pieces of code differ only in values, the difference is data, not code. This is the single
most valuable move available, and the one generated code misses most often.

```python
# ❌ dois métodos idênticos a menos das constantes
def getGyroData(self):  ...   # eixos wx, wy, wz
def getAccelData(self): ...   # o mesmo corpo, eixos ax, ay, az

# ✅ a diferença virou dado — adicionar um grupo agora é uma linha, não um método
requirements = {
    'gyro':  (['wx', 'wy', 'wz'], [('Ruído Estático STD (deg/s)', 'std_stat', 0.3)]),
    'accel': (['ax', 'ay', 'az'], [('Ruído Estático STD (m/s²)', 'std_stat', 0.05)]),
}

def get(self, group):
    axes, reqs = self.requirements[group]
    rows = [{'label': label, 'value': getMax(self.metrics, axes, key), 'required': req} for label, key, req in reqs]
    return [row for row in rows if row['value'] is not None]
```

The same idea in its other forms: reference tables and option menus live as data on the type, never
in an external file; a choice between variants is a sequence of conditions each returning its result,
or a lookup, never a chain of `elif`; an accumulation loop is a comprehension; an operation over a
whole array is expressed on the array, not element by element.

```python
vx[1:, 1:] += (dt / rho) * ((txx[1:, 1:] - txx[:-1, 1:]) / dx + (txz[1:, 1:] - txz[1:, :-1]) / dz)
```

**Fewer lines through better logic, never through compression.** Statements only share a line when
they form one logical unit — `plt.grid(alpha=.3); plt.legend(); plt.xlabel('time')`, `Xn2 = Xn1; Xn1 = Xn;`.
Unrelated statements glued together are not concise, they are unreadable.

---

## 7. Robustness is a shape, not a wrapper

Reliability comes from how the code is arranged, not from wrapping it in exception handling.

**Each failure mode gets its own guard that returns nothing meaningful, and the caller decides.**

```python
def get(self):
    self.conn.write(self.REQUEST)
    head = self.conn.read(7)

    if len(head) < 7 or head[:3] != self.HEADER:
        return None

    size = struct.unpack('<I', head[3:])[0]

    if size == 0 or size > self.MAX_SIZE:
        return None

    return cv2.imdecode(np.frombuffer(self.conn.read(size), np.uint8), cv2.IMREAD_COLOR)
```

The caller skips and keeps running: `if frame is None: continue`. No nesting, no flag variable, no
try around code that cannot fail.

- **Error handling belongs where errors actually happen** — one boundary, named exceptions, and a
  cleanup that always runs. Never a catch-all swallowing logic errors.
- **Fail soft on data, loud on setup.** A corrupt packet or a missing field is skipped and the process
  survives; a port that will not open or a missing model file stops with a clear message.
- **Cleanup is idempotent.** Stopping twice, or before starting, is never an error.
- **The physical world lives in named constants.** Boot delays, size limits, intervals, cutoffs,
  thresholds, patience — never a literal buried in a method. Real hardware drifts; the tuning knob
  has to exist even when the model is minimal.

**Simulate before delivering**, and say which cases were checked: empty input, a single element, zero
variance, the device absent or sending half a message, the first iteration with state still unset,
values at and just past the limits, and the same call made twice or out of order.

---

## 8. Nothing speculative

Every abstraction, parameter, flag, and layer needs a user that exists today. "It might be useful
later" is how a codebase becomes unmaintainable, and later can add it in less time than it costs to
carry it now. Deleting is worth more than adding.

The exception is never the safety net: validation at a real trust boundary — input from a device, a
network, a file chosen by a person — error handling that prevents data loss, and the calibration knob
of a physical system all stay, no matter how minimal the rest is.

---

## 9. Visual regularity

Code that looks like one hand wrote it is faster to read than code that is merely correct. Related
assignments align on the `=`; a complete statement stays on one line when it fits a comfortable width,
including calls with many arguments; a simple expression is never broken across lines. One blank line
separates logical blocks, never two in a row, and two separate a definition from the code that uses it.

```python
self.xData = xData
self.scores = {}
self.seed   = seed
self.r2 = (1.0 if ssRes == 0 else 0.0) if ssTot == 0 else 1 - float(ssRes / ssTot)
```

Members are declared in a stable order — constants, construction, core operations, business logic,
utilities, visual output, transformation, persistence — so any type in the project is scanned the
same way.

---

## 10. The same principles, per context

**Language.** Code in English. Comments, commits, documentation, notebook prose, chart titles and
end-user messages in Portuguese.

**Python.** No type annotations, no docstrings, no leading underscore. `None` as the default for an
optional action. Comprehensions and a named `lambda` where they read cleanly. Tabular results become
a DataFrame for display. A module that is a single service ends by instantiating it.

**Notebooks.** A numbered pipeline whose stages exchange files. First cell holds every import and the
global configuration; constants come right after the data is loaded; a library used by one section is
imported just before it. **The markdown cell is the section header** — uppercase Portuguese, LaTeX and
bullets for theory — so a banner comment inside code never exists. One cell, one step: a class is
defined, instantiated and used in the same cell, and every cell ends with visible proof — a table as
the final expression, a chart, or a short metric print, never a mute cell. Charts are wide with
side-by-side panels, always carry grid, title and legend, and take an optional save path. A class that
stabilizes moves to a module with the same API.

**Embedded (ESP32 / Arduino).** The file tree mirrors the physical composition of the device: every
component is a folder with a header as its entry point, and a subcomponent is a subfolder. A component
receives its parent by template pointer and reaches siblings through it — no singletons, no globals.
Every component has `setup()` and `handle()`, and the parent propagates both. Rate limiting uses a
static timer inside the method that needs it. Members are public by default, with behavior flags for
debug and bypass. Protocol and UI constants are grouped by prefix in one globals header; per-type
constants stay on the type. A stored member is a fixed buffer, never a dynamic string — as a local or
a return value a dynamic string is fine.

```cpp
template <typename Parent> class AlcoholSensor{
  private:
    Parent* device;

  public:
    bool debug;
    Text<20> id;
    Heater<Parent> heater;

    AlcoholSensor(Parent* dev): device(dev), heater(dev){}

    void setup(){
        heater.setup();
        device->telemetry.event("$ETEV09" + id.toString() + "!");
    }
};
```

**JavaScript / TypeScript.** Immutable binding by default, mutable only when reassigned, never the
legacy keyword. Arrow functions for callbacks, template literals over concatenation, destructuring
where it simplifies. Types only where the compiler demands them.

**SQL.** Keywords uppercase, tables `PascalCase`, columns `snake_case`.

---

## 11. Before delivering

1. Does every definition carry its one uppercase line, saying what the name does not?
2. Zero docstrings, type annotations, leading underscores, comment blocks, banners?
3. Is every type justified by state shared across operations? Would a function do?
4. Is any repeated block actually a difference in data? Any element-by-element loop that is an array
   operation? Any single-use intermediate that can be inlined?
5. Any dead code — an unused import, a parameter never read, a knob with one real value, a layer
   nobody asked for?
6. Do names follow the owner of the concept, short, with the recurring ones reused?
7. Standard vocabulary, light construction, heavy `update()`?
8. Guards and returns instead of defensive wrapping; error handling at the real boundary; cleanup
   idempotent; physical constants named?
9. Simulated against empty, single, degenerate, absent, first, repeated — and said so?
10. Aligned assignments, complete statements on one line, one blank line between blocks?
11. Does it follow the structure already in the project and reuse what was already there?
