# Human-like input

Botonomus can shape pointer, keyboard and wheel input like a person's. All events are trusted browser input dispatched through CDP `Input`; nothing is injected into the page. This shapes timing and trajectories only. Behavioural classifiers can still distinguish automation, and humanizing is opt-in.

## Automatic: `humanize=True`

```python
from botonomus import Botonomus, BrowserConfig

config = BrowserConfig(humanize=True)
async with Botonomus(config=config) as bot, bot.open(profile="p1") as session:
    page = session.page  # a HumanPage
    await page.goto("https://example.com/login")  # delegated unchanged
    await page.click("text=Sign in")  # humanized
    await page.fill("#email", "user@example.com")  # keystroke by keystroke
    await page.fill("#password", "correct horse", sensitive=True)
    await page.press("Enter")
    await page.scroll(600)
    await page.raw.locator("#done").click()  # .raw bypasses humanizing
```

With `humanize` set, `session.page` is a [`HumanPage`](../reference/human.md):

| Kind | Methods |
|---|---|
| Humanized on `HumanPage` | `click`, `fill`, `type`, `press`, `hover`, `scroll` |
| Return `HumanLocator` | `locator`, `get_by_role`, `get_by_text`, `get_by_label`, `get_by_placeholder`, `get_by_alt_text`, `get_by_title`, `get_by_test_id` (when the driver has them) |
| Humanized on `HumanLocator` | `click`, `fill`, `type`, `press`, `hover`; `nth` and `first` stay wrapped |
| Delegated unchanged | Everything else: `goto`, `evaluate`, `title`, `screenshot`, `mouse`, `keyboard`, ... |

`page.raw` is the unwrapped page and `page.human` the underlying [`Human`](../reference/human.md) engine. `press` accepts both `press("Enter")` and the Playwright form `press("#q", "Enter")`. The native driver provides `get_by_role` and `get_by_text`; the other `get_by_*` factories need a Playwright-based driver.

## Manual: `Human`

Use `Human` directly when you want humanized input on some actions only:

```python
from botonomus import Human

human = Human(session.page, seed=1234)  # seed for reproducible paths and timing
await human.click(session.page.get_by_role("button", name="Sign in"))
await human.type("hello@example.com", session.page.locator("#email"))
await human.fill(session.page.locator("#search"), "weather tomorrow")
await human.scroll(800)
await human.move_to(400, 300)
await human.idle(1.5)  # small pointer drift around the current position
await human.pause()  # a "thinking" pause; sends no CDP traffic
```

Targets are locators from either driver, or `(x, y)` points. Before acting on a locator, `Human` waits until it is visible, enabled and has a stable box (`actionability_timeout`, default 10 s), then brings it into view with wheel scrolling. A target that never becomes actionable raises `NotActionableError`.

## What the motion looks like

- **Pointer:** eased cubic Bézier paths with Fitts's-law durations; long moves sometimes overshoot and correct; clicks land near, not exactly on, the element centre and hold the button for a natural interval.
- **Keyboard:** one key at a time with lognormal gaps, a per-session typing tempo that drifts slowly, faster common letter pairs, longer pauses after spaces and punctuation, and occasional hesitations.
- **Mistakes:** with probability `mistype_rate` per letter, a neighbouring key on the configured layout is hit, noticed a character or two later and corrected with Backspace. The field always ends up with exactly the intended text. `sensitive=True` disables mistakes for passwords, one-time codes and other secrets, where a transient wrong character can trigger validation, lockouts or be captured by a key logger on the page.
- **Wheel:** scrolls arrive in several eased increments like a physical wheel.
- **Idle:** with `idle_moves=True` the pointer drifts slightly between actions.

## Presets and tuning

```python
from botonomus import BrowserConfig, HumanConfig

HumanConfig.preset("default")  # moderate speed, occasional slips, no idle drift
HumanConfig.preset("careful")  # slower, longer thinking, idle drift, more overshoot
HumanConfig.preset("fast")  # quick, practised input, fewer slips and pauses

config = BrowserConfig(humanize=HumanConfig.preset("careful", mistype_rate=0.0))
tuned = HumanConfig().replace(key_delay=0.09, layout="de")
```

```pycon
>>> from botonomus import HumanConfig
>>> HumanConfig.preset("fast").key_delay
0.075
>>> HumanConfig.preset("careful", mistype_rate=0.0).mistype_rate
0.0
```

Every random interval is drawn from a right-skewed (lognormal) distribution; two-value ranges such as `think=(0.15, 0.45)` give the typical span, not hard uniform bounds. Invalid values raise `ValueError` on construction. The full field list is in the [`HumanConfig` reference](../reference/human.md).

### Keyboard layouts

Mistypes pick physically neighbouring keys from the layout: `"us"` (QWERTY, default), `"de"` (QWERTZ) or `"fr"` (AZERTY). Register your own with `botonomus.human.register_layout(KeyboardLayout(...))` and refer to it by name.

`HumanProfile` is a deprecated alias of `HumanConfig` kept for 0.1 code, and `Human(page, profile=...)` a deprecated alias of `config=`.
