from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, send_file
import json
import os
import io
import time
import secrets
import random
import qrcode
from datetime import datetime
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq

# =====================================================
# CONFIG
# =====================================================
# The Groq API key must be set as an environment variable, e.g.:
#   export GROQ_API_KEY="your-key-here"        (Linux/macOS)
#   setx GROQ_API_KEY "your-key-here"           (Windows)
# Get a free key at https://console.groq.com/keys
# Never hardcode API keys directly in source files.
if "GROQ_API_KEY" not in os.environ:
    raise RuntimeError(
        "GROQ_API_KEY environment variable is not set. "
        "Set it before running the app, e.g. `export GROQ_API_KEY=your-key`."
    )

llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0.7)

app = Flask(__name__, template_folder="template")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

# Public address of this site (e.g. your ngrok URL). When set, every QR code uses it,
# even if you are browsing on localhost, so phones and the cloud scanner can reach it.
DEFAULT_PUBLIC_URL = "https://negotiate-sauciness-punk.ngrok-free.dev"
# Set the PUBLIC_BASE_URL environment variable to use a different address, or to "" to turn this off.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", DEFAULT_PUBLIC_URL).strip().rstrip("/")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

ORDER_CONTACTS_FILE = os.path.join(DATA_DIR, "order_contacts.json")
CHAT_LOG_FILE = os.path.join(DATA_DIR, "chat_log.json")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are FoodOrder's helpful AI assistant. Answer the user's questions about food, delivery, or general recommendations politely."),
    ("user", "{input}")
])

chain = prompt | llm


def invoke_with_retry(payload, max_retries=3, base_delay=2):
    """Call the LLM chain, retrying with exponential backoff on 429 rate limits."""
    last_error = None
    for attempt in range(max_retries):
        try:
            return chain.invoke(payload)
        except Exception as e:
            last_error = e
            is_rate_limited = "429" in str(e) or "rate_limited" in str(e).lower()
            if is_rate_limited and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)  # 2s, 4s, 8s...
                time.sleep(delay)
                continue
            raise last_error


# =====================================================
# DEMO PRODUCT DATA
# Replace this with real data from a database/table once you have one.
# =====================================================
PRODUCTS = {
    1: {
        "id": 1,
        "name": "Classic Cheese Burger",
        "category": "Burgers",
        "price": 899,
        "old_price": 1099,
        "badge": "Best Seller",
        "rating": 4.5,
        "review_count": 128,
        "prep_time": "15-20 min",
        "calories": 650,
        "protein": 32,
        "carbs": 45,
        "fat": 38,
        "spicy": False,
        "vegetarian": False,
        "description": "A juicy grilled beef patty topped with melted cheddar, fresh lettuce, tomato, and our signature sauce, served in a toasted brioche bun.",
        "ingredients_note": "Made fresh daily with locally-sourced beef and produce.",
        "ingredients": ["Beef patty", "Cheddar cheese", "Brioche bun", "Lettuce", "Tomato", "Onion", "House sauce", "Pickles"],
        "images": ["burger.jpg", "image.jpg", "red.jpg"],
        "reviews": [
            {"name": "Sarah M.", "rating": 5, "comment": "Best burger in town, always fresh and hot!"},
            {"name": "James T.", "rating": 4, "comment": "Great taste, delivery could be a bit faster."},
            {"name": "Aisha K.", "rating": 5, "comment": "My go-to order every week."},
        ],
    },
    2: {
        "id": 2,
        "name": "Pepperoni Pizza",
        "category": "Pizza",
        "price": 1299,
        "old_price": None,
        "badge": "New",
        "rating": 4.7,
        "review_count": 96,
        "prep_time": "20-25 min",
        "calories": 780,
        "protein": 28,
        "carbs": 82,
        "fat": 34,
        "spicy": False,
        "vegetarian": False,
        "description": "A hand-tossed pizza loaded with spicy pepperoni, mozzarella, and our slow-simmered tomato sauce, baked until golden.",
        "ingredients_note": "Stone-baked using traditional Italian methods.",
        "ingredients": ["Pizza dough", "Mozzarella", "Pepperoni", "Tomato sauce", "Oregano", "Olive oil"],
        "images": ["pizza1.jpg", "image.jpg"],
        "reviews": [
            {"name": "Daniel R.", "rating": 5, "comment": "Crust is perfect, cheese pull is amazing."},
            {"name": "Lena W.", "rating": 4, "comment": "Really tasty, a bit greasy for my liking."},
        ],
    },
}


def get_related_products(current_id, limit=3):
    return [p for pid, p in PRODUCTS.items() if pid != current_id][:limit]


# =====================================================
# CART HELPERS
# =====================================================
def find_product(product_id):
    return PRODUCTS_CATALOG.get(product_id) or PRODUCTS.get(product_id)


def get_cart_items():
    """Resolve the session cart (id -> qty) into full item dicts with subtotal."""
    cart = session.get("cart", {})
    items = []
    total = 0
    for pid_str, qty in cart.items():
        product = find_product(int(pid_str))
        if not product:
            continue
        subtotal = product["price"] * qty
        total += subtotal
        items.append({
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "image": product["images"][0] if product.get("images") else "image.png",
            "qty": qty,
            "subtotal": subtotal,
        })
    return items, total


def get_cart_count():
    return sum(session.get("cart", {}).values())


@app.context_processor
def inject_cart():
    """Makes cart_count / cart_items / cart_total available in every template
    so the cart icon + drawer can render on any page without extra plumbing."""
    items, total = get_cart_items()
    return {"cart_count": get_cart_count(), "cart_items": items, "cart_total": total,
            "public_base_url": PUBLIC_BASE_URL}


# =====================================================
# DEMO CATALOG GENERATOR
# Simulates a large product catalog (e.g. thousands of items) so pagination,
# search, and filtering can be tested at scale. Replace this with a real
# database query (e.g. SQLAlchemy .paginate()) once you have actual product
# data in a table.
# =====================================================
_CATEGORIES = ["Burgers", "Pizza", "Sushi", "Salads", "Desserts", "Drinks", "Pasta", "Tacos"]
_ADJECTIVES = ["Classic", "Spicy", "Deluxe", "Crispy", "Smoky", "Zesty", "Loaded", "Garden", "Grilled", "Cheesy"]
_IMAGES_BY_CATEGORY = {
    "Burgers": "image.png",
    "Pizza": "pizza1.jpg",
    "Sushi": "sushi.png",
    "Salads": "salads.png",
    "Desserts": "desserts.png",
    "Drinks": "drinks.png",
    "Pasta": "pasta.png",
    "Tacos": "tacos.png",
}


def generate_catalog(count=2000):
    """Generate a large demo catalog in memory, deterministically."""
    catalog = {}
    for i in range(1, count + 1):
        category = _CATEGORIES[i % len(_CATEGORIES)]
        adjective = _ADJECTIVES[i % len(_ADJECTIVES)]
        price = 200 + (i % 40) * 45  # PKR, roughly 200-1955
        rating = round(3.0 + (i % 20) / 10, 1)
        catalog[i] = {
            "id": i,
            "name": f"{adjective} {category[:-1] if category.endswith('s') else category} #{i}",
            "category": category,
            "price": price,
            "old_price": price + 150 if i % 5 == 0 else None,
            "badge": "New" if i % 17 == 0 else ("Best Seller" if i % 23 == 0 else None),
            "rating": min(rating, 5.0),
            "review_count": (i * 7) % 500,
            "prep_time": "15-20 min",
            "calories": 400 + (i % 500),
            "protein": 10 + (i % 30),
            "carbs": 20 + (i % 60),
            "fat": 5 + (i % 25),
            "spicy": i % 4 == 0,
            "vegetarian": i % 6 == 0,
            "description": f"A delicious {adjective.lower()} {category.lower()[:-1]} made fresh to order.",
            "ingredients_note": "Made fresh daily with quality ingredients.",
            "ingredients": ["Fresh ingredients", "House sauce", "Seasoning"],
            "images": [_IMAGES_BY_CATEGORY.get(category, "image.png")],
            "reviews": [],
        }
    return catalog


# Demo catalog with 2,000 items. Swap PRODUCTS_CATALOG for a real DB-backed
# query in production; keep pagination/search logic in the route the same.
PRODUCTS_CATALOG = generate_catalog(2000)
PAGE_SIZE = 24


# =====================================================
# JSON FILE STORAGE
# Order contact submissions and chat transcripts are appended to flat JSON
# files under data/ instead of a database. Fine for a demo; swap for a real
# database if this needs to handle concurrent writes at scale.
# =====================================================
def _read_json_list(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _append_json_list(path, entry):
    items = _read_json_list(path)
    items.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    return items


def save_submission():
    """Validate and save the incoming contact/order form data. Returns True on success."""
    name = request.form.get("name", "").strip()
    number = request.form.get("number", "").strip()
    email = request.form.get("email", "").strip()
    message = request.form.get("message", "").strip()

    if not all([name, number, email, message]):
        flash("All fields are required.", "error")
        return False

    _append_json_list(ORDER_CONTACTS_FILE, {
        "name": name,
        "number": number,
        "email": email,
        "message": message,
        "created_at": datetime.utcnow().isoformat(),
    })

    flash("Your message has been sent. Thank you!", "success")
    return True


# =====================================================
# FLASK ROUTES
# =====================================================
@app.route('/')
def home():
    featured_products = list(PRODUCTS.values()) + list(PRODUCTS_CATALOG.values())[:2]
    return render_template('index.html', featured_products=featured_products)


@app.route("/product/<int:product_id>")
def product_page(product_id):
    # Look in the demo catalog first (falls back to the hand-written PRODUCTS demo items)
    product = PRODUCTS_CATALOG.get(product_id) or PRODUCTS.get(product_id)
    if not product:
        return "Product not found", 404
    related = list(PRODUCTS_CATALOG.values())[:3]
    return render_template("product.html", product=product, related_products=related)


@app.route("/products")
def products_page():
    query = request.args.get("q", "").strip()
    selected_category = request.args.get("category", "").strip()
    sort = request.args.get("sort", "").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    items = list(PRODUCTS_CATALOG.values())

    # --- Search ---
    if query:
        q_lower = query.lower()
        items = [p for p in items if q_lower in p["name"].lower() or q_lower in p["category"].lower()]

    # --- Category filter ---
    if selected_category:
        items = [p for p in items if p["category"] == selected_category]

    # --- Sort ---
    if sort == "price_asc":
        items.sort(key=lambda p: p["price"])
    elif sort == "price_desc":
        items.sort(key=lambda p: p["price"], reverse=True)
    elif sort == "rating":
        items.sort(key=lambda p: p["rating"], reverse=True)

    total_count = len(items)
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = items[start:end]

    start_index = start + 1 if total_count else 0
    end_index = min(end, total_count)

    # Build a compact page range like [1, '...', 4, 5, 6, '...', 80]
    page_range = build_page_range(page, total_pages)

    return render_template(
        "products.html",
        products=page_items,
        categories=_CATEGORIES,
        query=query,
        selected_category=selected_category,
        sort=sort,
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        start_index=start_index,
        end_index=end_index,
        page_range=page_range,
    )


def build_page_range(current, total, window=2):
    """Return a compact list of page numbers/ellipses for pagination controls."""
    if total <= 7:
        return list(range(1, total + 1))

    pages = {1, total, current}
    for offset in range(1, window + 1):
        pages.add(current - offset)
        pages.add(current + offset)
    pages = sorted(p for p in pages if 1 <= p <= total)

    result = []
    prev = None
    for p in pages:
        if prev is not None and p - prev > 1:
            result.append("...")
        result.append(p)
        prev = p
    return result


@app.route("/contact", methods=["GET", "POST"])
def contact_form():
    if request.method == "POST":
        save_submission()
        return redirect(url_for("contact_form"))
    return render_template("contact.html")


@app.route("/submit", methods=["GET", "POST"])
def submit_form():
    if request.method == "POST":
        save_submission()
    return redirect(url_for("contact_form"))


@app.route("/submissions", methods=["GET"])
def list_submissions():
    """Simple JSON endpoint to view stored order-contact submissions (e.g. for an admin view)."""
    submissions = _read_json_list(ORDER_CONTACTS_FILE)
    return jsonify(list(reversed(submissions)))


# =====================================================
# CART + CHECKOUT ROUTES
# =====================================================
@app.route("/cart/add/<int:product_id>", methods=["POST"])
def add_to_cart(product_id):
    product = find_product(product_id)
    if not product:
        return jsonify({"error": "Product not found"}), 404

    cart = session.get("cart", {})
    key = str(product_id)
    cart[key] = cart.get(key, 0) + 1
    session["cart"] = cart

    items, total = get_cart_items()
    return jsonify({
        "cart_count": get_cart_count(),
        "cart_total": total,
        "added": product["name"],
        "drawer_html": render_template("_cart_drawer_items.html", cart_items=items, cart_total=total),
    })


@app.route("/cart/remove/<int:product_id>", methods=["POST"])
def remove_from_cart(product_id):
    cart = session.get("cart", {})
    cart.pop(str(product_id), None)
    session["cart"] = cart

    items, total = get_cart_items()
    return jsonify({
        "cart_count": get_cart_count(),
        "cart_total": total,
        "drawer_html": render_template("_cart_drawer_items.html", cart_items=items, cart_total=total),
    })


@app.route("/cart/update/<int:product_id>", methods=["POST"])
def update_cart_qty(product_id):
    """Set an exact quantity (used by +/- steppers in the drawer)."""
    qty = request.form.get("qty", type=int)
    cart = session.get("cart", {})
    key = str(product_id)
    if qty is None or qty < 1:
        cart.pop(key, None)
    else:
        cart[key] = qty
    session["cart"] = cart

    items, total = get_cart_items()
    return jsonify({
        "cart_count": get_cart_count(),
        "cart_total": total,
        "drawer_html": render_template("_cart_drawer_items.html", cart_items=items, cart_total=total),
    })


@app.route("/checkout/confirm", methods=["POST"])
def confirm_order():
    items, total = get_cart_items()
    if not items:
        flash("Your cart is empty.", "error")
        return redirect(url_for("home"))

    # Simulated rider ETA — swap for a real dispatch/logistics estimate later.
    eta_minutes = random.randint(20, 45)

    order = {
        "order_id": f"ORD-{secrets.token_hex(6).upper()}",
        "line_items": items,
        "total": total,
        "eta_minutes": eta_minutes,
        "placed_at": datetime.utcnow().isoformat(),
    }

    # Persist the order to disk so it survives past this browser session.
    _append_json_list(ORDERS_FILE, order)

    session["last_order"] = order
    session["cart"] = {}  # empty the cart now that the order is placed

    return redirect(url_for("view_order", order_id=order["order_id"]))


@app.route("/order/status")
def order_status():
    order = session.get("last_order")
    if not order:
        flash("No active order found.", "error")
        return redirect(url_for("home"))
    return render_template("order_status.html", order=order)


@app.route("/qr")
def generate_qr():
    """Renders a PNG QR code for whatever URL/text is passed in ?data=.
    Used by the product page to turn the 'order now' link into a scannable code."""
    data = request.args.get("data", "").strip()
    if not data:
        return "Missing 'data' query parameter", 400

    img = qrcode.make(data, error_correction=qrcode.constants.ERROR_CORRECT_H)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/order/quick-confirm/<int:product_id>", methods=["GET"])
def quick_confirm_order(product_id):
    """Finalizes an order straight from delivery details captured on the product
    page. This is the URL encoded into the QR code shown after 'Order Now' —
    scanning/opening it places the order and lands on the rider ETA screen."""
    product = find_product(product_id)
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for("home"))

    qty = request.args.get("qty", 1, type=int) or 1
    name = request.args.get("name", "").strip()
    phone = request.args.get("phone", "").strip()
    address = request.args.get("address", "").strip()

    subtotal = product["price"] * qty
    eta_minutes = random.randint(20, 45)

    order = {
        "order_id": f"ORD-{secrets.token_hex(6).upper()}",
        "line_items": [{
            "id": product["id"],
            "name": product["name"],
            "price": product["price"],
            "image": product["images"][0] if product.get("images") else "image.png",
            "qty": qty,
            "subtotal": subtotal,
        }],
        "total": subtotal,
        "eta_minutes": eta_minutes,
        "placed_at": datetime.utcnow().isoformat(),
        "delivery": {"name": name, "phone": phone, "address": address},
    }

    _append_json_list(ORDERS_FILE, order)
    session["last_order"] = order

    return redirect(url_for("view_order", order_id=order["order_id"]))


def find_order_by_id(order_id):
    return next((o for o in _read_json_list(ORDERS_FILE) if o.get("order_id") == order_id), None)


@app.route("/api/order/<order_id>")
def api_order(order_id):
    """JSON for one order. Used by qr_scanner.py so it can look up an order
    from any machine, not just the one that has data/orders.json."""
    order = find_order_by_id(order_id)
    if not order:
        return jsonify({"error": "Order not found"}), 404
    return jsonify(order)


@app.route("/order/<order_id>")
def view_order(order_id):
    """Permanent order page (loaded from orders.json) so the QR works on any device."""
    order = find_order_by_id(order_id)
    if not order:
        flash("Order not found.", "error")
        return redirect(url_for("home"))
    if PUBLIC_BASE_URL:
        order_url = PUBLIC_BASE_URL + url_for("view_order", order_id=order_id)
    else:
        order_url = url_for("view_order", order_id=order_id, _external=True)
    return render_template("order_status.html", order=order, order_url=order_url)


@app.route("/orders", methods=["GET"])
def list_orders():
    """Simple JSON endpoint to view all confirmed orders saved to disk."""
    return jsonify(list(reversed(_read_json_list(ORDERS_FILE))))


# =====================================================
# AI LANGCHAIN API ENDPOINT (Matched to /chat)
# =====================================================
@app.route('/chat', methods=['POST'])
def chat():
    """Endpoint for the front-end AI chat window widget."""
    user_message = request.json.get('message', '')
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    try:
        response = invoke_with_retry({"input": user_message})
        answer = response.content
        _append_json_list(CHAT_LOG_FILE, {
            "message": user_message,
            "response": answer,
            "created_at": datetime.utcnow().isoformat(),
        })
        return jsonify({"response": answer})
    except Exception as e:
        import traceback
        traceback.print_exc()  # prints the full stack trace to your terminal

        if "429" in str(e) or "rate_limited" in str(e).lower():
            friendly = "I'm getting a lot of questions right now — please try again in a moment."
        else:
            friendly = "Sorry, I couldn't process that. Please try again."

        _append_json_list(CHAT_LOG_FILE, {
            "message": user_message,
            "response": None,
            "error": str(e),
            "created_at": datetime.utcnow().isoformat(),
        })

        return jsonify({"error": friendly, "debug": str(e)}), 500


@app.route('/chat/log', methods=['GET'])
def chat_log():
    """Simple JSON endpoint to view stored chat transcripts (e.g. for an admin view)."""
    return jsonify(list(reversed(_read_json_list(CHAT_LOG_FILE))))


if __name__ == "__main__":
    app.run(debug=True, port=35000)