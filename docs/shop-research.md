# How BD f-shops really take orders, and what the shop agent should change

Written 21 Oct 2026. *Built the same day as D92 (PLAN.md day S1): every fix below is in the
code. The rest of this file is the research as written.*

**Short answer:** a real Facebook shop asks for everything in one message, works out the
delivery area from the address, and confirms an order **one way only**: an advance on the
delivery charge (usually only outside Dhaka) **or** a phone call **or** nothing at all. Our
agent asks for the details one at a time, asks for the area separately, and with pay-first on
it takes the advance **and** promises a call. The fixes are mostly prompt lines and one setting.

## What real shops do

Sources are shop policy pages, courier guides and seller articles (listed at the end). They
agree with each other, but none is a survey, so read them as "the usual way", not a rule.

**1. Confirming an order: one of three, never two.**

| Way | What the buyer hears | Where it's used |
|---|---|---|
| **Advance delivery charge** | "Send the delivery charge (৳100–150) by bKash to confirm. The rest is cash on delivery." Once it's paid, the order is confirmed. No call. | Mostly **outside Dhaka**, where returns cost the most. Some shops ask it everywhere, or add more for orders over ~৳4,000 |
| **A phone call** | "We'll call you to confirm the order." | Shops with no advance, and high-value orders |
| **Nothing** | "Your order is confirmed. Pay the full amount to the delivery man." | Small shops, and inside Dhaka |

Typical published rules: Borno Shop is cash on delivery inside Dhaka (৳60) and takes a ৳120
courier charge by bKash first outside Dhaka. Himel Shop does the same with ৳60 and ৳100.
Ajkershop says "the order is confirmed only after the advance payment is received" (৳100 in
Dhaka, ৳150 outside, +৳100 over ৳4,000). Sites with no advance say "we'll call to confirm, and
you pay when the product is in your hands". A call on top of a paid advance isn't something
any of them do: the advance *is* the confirmation.

**2. Taking the details: one message.** Pages post "inbox your name, full address, phone number
and the product's name". Couriers' tools (CourierBD) exist to turn that one messy message into
a dispatch form. Nobody asks for the delivery area as its own question: "Mirpur 10" or
"Sylhet sadar" already says inside or outside Dhaka. The courier needs the district and thana;
a landmark is a nice-to-have, not something sellers insist on.

**3. Checking at the door, and refusing.** Most COD shops let the buyer open the parcel in front
of the rider. If they refuse it anyway, they pay the delivery charge to the rider and the parcel
goes back ("even if the product matches the photo, if you refuse it you pay the ৳60"). The
advance, when taken, isn't refunded on a cancel. Buyers ask about this ("check kore nite
parbo?") before they order.

**4. After the order.** The seller sends a short summary, the buyer says "ok" or "confirm". The
rider phones before he comes. "Where is my parcel?" is the most common message afterwards, and
buyers give their **phone number**, not an order code: shops look orders up by number. Before
the parcel ships, shops simply change the items in the inbox.

**5. Fake orders are the fear.** Couriers (Pathao, Steadfast) tell sellers to check the
number's delivery history (we do, D91), call big orders, and take an advance. That's why the
advance exists, and why it's per area: a refused parcel outside Dhaka costs twice the courier
charge.

## What our agent does today, and what to change

Most important first. "Size" is a rough guess of the work.

### 1. Pay-first and the call both happen (the bug you saw)

**Now:** with `pay_first` on, `place_order` takes the delivery charge online, the payment check
moves the order to `new`, and `ShopOrder.after()` tells the buyer "The shop will call you to
confirm before sending it" ([models.py:77](../src/heydesk/desks/shop/models.py#L77)).
`STATUS_WORDS["new"]` says the same to anyone who tracks it. Worse, if calls are on and the
buyer writes again after paying, `CallService.after_turn` finds the order `new` and queues a
call for it ([calls.py:96](../src/heydesk/services/calls.py#L96)).

**Also now:** with pay-first **off** and calls **off**, the agent still promises "the shop will
call {phone} to confirm" ([tools.py](../src/heydesk/desks/shop/tools.py), `place_order`'s
`next`). Many shops never call, so that's a promise nobody keeps.

**Fix (small):** each order gets exactly one way to be confirmed:

- **Paid the advance:** the payment check makes the order `confirmed`, not `new`. The buyer
  hears "Payment received. Your order is confirmed and will be sent in 2–3 days; pay ৳X to the
  rider." It's never queued for a call.
- **Cash on delivery, the shop calls** (calls on, or the owner calls by hand): "We'll call you
  to confirm." As now.
- **Cash on delivery, no call:** the order goes straight to `confirmed`, and the buyer hears
  "Your order is confirmed. Pay ৳X to the delivery man."

That needs one owner setting: **how do you confirm an order? advance / call / no confirmation.**
The `new` status words and the call's opening line follow from it.

### 2. The advance should be per delivery area

**Now:** `pay_first` is one switch for the whole shop.
**Real shops:** cash on delivery inside Dhaka, advance outside Dhaka.
**Fix (small to medium):** a `pay_first` tick on each `Zone` instead of the shop. `place_order`
already knows the zone before the read-back, so the read-back can say the right thing for that
buyer. This and fix 1 together are one decision (a line in decisions.md replacing D47's shop-wide
switch).

### 3. Ask for everything in one message

**Now:** the prompt says "ask only for what's still missing, **one at a time**"
([shop/\_\_init\_\_.py](../src/heydesk/desks/shop/__init__.py), *Taking an order* step 2). So
it's four turns: area, address, name, mobile.
**Fix (a prompt line):** on text channels, ask for all of it in one message, like a shop does:
"To confirm the order, please send your name, mobile number and full address (with thana and
district)." Then ask only for what's still missing, all together. **On a call keep one at a
time**: that's natural on the phone. The channel is already in *Right now*, so one line says it.

### 4. Don't ask for the area, and don't insist on a landmark

**Now:** the prompt lists "where to deliver (the area…)" as its own item, and the address must
have "house, road, area, district and a landmark".
**Fix (prompt and a field description):** take the area from the address (`match_zone` already
matches places in it, "Mirpur 10" → Inside Dhaka). Ask "inside or outside Dhaka?" only when
`place_order` says it can't tell. Ask for house/road, thana and district; make the landmark
optional.

### 5. Track an order by mobile number

**Now:** tracking another order needs the code (O-104) **and** the mobile. Buyers don't keep
codes.
**Fix (medium):** find by the mobile alone, the latest order or a short list. To keep it safe,
answer with the status and items only, never the address. On Messenger and Telegram, the
buyer's own past orders are already known (D81), so no number is needed there at all.

### 6. Change the items before it ships

**Now:** "To change the items, they cancel it and order again."
**Real shops:** change it in the inbox.
**Fix (medium):** `change_order` takes new lines (checked like `update_cart`) while the order is
`new` or `confirmed`. Python works out the new total, read back, yes, save. A paid advance
stays as it is, unless the new subtotal crosses free delivery, which is rare; take a message
for the team then.

### 7. Things buyers ask that the agent can't answer

"Can I check it before paying?", "What if I don't like it?", "Is the advance refunded?" The
agent only knows these if the owner wrote them in *Policies*.
**Fix (tiny, no code):** the *Policies* box's placeholder on the owner's page asks for exactly
these three, with an example: "You can open the parcel in front of the rider. If you refuse it,
pay the delivery charge to the rider. The advance isn't refunded on a cancel." Add
fields only if owners keep leaving it empty.

### 8. The read-back and the "yes"

**Now:** one long line: "1 × Panjabi (L, White) = ৳1,450; delivery Inside Dhaka ৳60; total
৳1,510, paid in cash on delivery; to …; name …; mobile …".
**Real shops** send a short block, one item per line, then "Confirm korbo?". On Messenger and
Telegram (plain text, no markdown), the prompt can ask for one detail per line.
**To test:** BD buyers say yes as "ji", "hmm", "ok", "thik ache", "confirm", "dao". Check that
Jev takes each as a yes, and "dekhi" ("let me see") or "pore janabo" as not one. These belong
in the 30-conversation test set (product-gaps §6).

### 9. Sound like a BD shop

- **Tone (a prompt line):** polite *apni*, "ji", and no "Hi there!" English cheer in Bangla
  replies.
- **Greeting:** the default lists features ("I can tell you about our products and prices,
  take your order, or check on one"). Shops just say "Assalamu alaikum, how can we help?".
  The owner can edit it, but the default is what most will keep.
- **After the order:** add "the delivery man will call before he comes", which is what
  buyers expect to hear and stops "nobody called me" worries.
- **Paying the advance:** say what the link takes ("bKash, Nagad or card"). Buyers expect
  bKash and may not trust an unexplained link.

## Already right (keep it)

Prices said openly (buyers hate "inbox for price"), cash on delivery by default, charges per
area with places matched, the free-delivery nudge, photo matching for screenshots, reusing a
returning buyer's details, and the courier fraud score to the owner.

## Suggested order

1. Fixes 1 and 2 together: one way to confirm, the advance per area (one decision).
2. Fixes 3 and 4: one message for the details, area from the address (prompt only, an hour).
3. Fix 7's placeholder and fix 9's lines (prompt and owner page, an hour).
4. Fix 5, tracking by mobile.
5. Fix 6, changing items.
6. Fix 8's "yes" words in the test set.

## Sources

- Shop policies: [Borno Shop](https://bornoshop.com/delivery-policy) ·
  [Himel Shop](http://m.himelshop.com/delivery-payment) ·
  [Ajkershop COD](https://ajkershop.com/COD) ·
  [Happy Purchase BD](https://happypurchasebd.com/delivery-policy) ·
  [Fast Fashion BD](https://www.fastfashionbd.com/policies/shipping-policy) ·
  [Khaasfood](https://www.khaasfood.com/delivery-policy/) ·
  [Dolonchapa order page](https://dolonchapa.com/order) ·
  [F10 Departmental Store](https://f10bd.com/contact-us/) ·
  [Origin Electronics, bKash confirmation](https://origin.com.bd/product/order-confirmation-by-bkash/)
- Couriers and sellers: [Pathao, avoiding courier fraud](https://pathao.com/blog/avoid-courier-fraud-bangladesh/) ·
  [Drix, couriers for f-commerce](https://drix.app/blog/best-courier-for-f-commerce-sellers) ·
  [CourierBD](https://www.courierbd.com/) ·
  [Future Startup, fake orders](https://futurestartup.com/2017/01/31/practical-tips-deal-fake-orders-e-commerce-f-commerce/) ·
  [Designing checkout for 85% COD (DEV)](https://dev.to/kingrayhan/designing-checkout-for-a-market-where-85-of-orders-are-cash-on-delivery-3iin) ·
  [ManyDial, automated confirmation calls](https://www.manydial.com/case-studies/ecommerce) ·
  [Sukkar Shop, why bKash and COD cost extra](https://sukkarshop.com/blog/8405/extra-fee-keno-lage/)
- Checking at the door: [Lock.pub, Daraz safety](https://lock.pub/en/blog/daraz-online-shopping-scam) ·
  [Solve Store BD](https://solvestorebd.com/blog/7-things-to-check-before-buying-a-product-online-in-bangladesh)
- Background: [DHL, f-commerce in Bangladesh](https://www.dhl.com/discover/en-bd/e-commerce-advice/e-commerce-trends/guide-to-f-commerce-in-bangladesh) ·
  [UNB, f-commerce problems](https://unb.com.bd/category/Business/f-commerce-in-bangladesh-problems-and-prospects/77360)
