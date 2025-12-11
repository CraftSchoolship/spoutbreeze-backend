"""
Stripe Helper Script
This script helps you find the correct Price IDs from your Stripe Products
"""

import stripe
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

print("🔍 Fetching your Stripe Products and their Price IDs...\n")
print("=" * 80)

try:
    # Get all products
    products = stripe.Product.list(limit=100, active=True)

    if not products.data:
        print("❌ No products found in your Stripe account.")
        print("   Please create products in Stripe Dashboard first.")
    else:
        print(f"✅ Found {len(products.data)} product(s) in your Stripe account:\n")

        for product in products.data:
            print(f"📦 Product: {product.name}")
            print(f"   Product ID: {product.id}")
            print(f"   Description: {product.description or 'No description'}")

            # Get prices for this product
            prices = stripe.Price.list(product=product.id, active=True)

            if prices.data:
                print("   💰 Prices:")
                for price in prices.data:
                    amount = price.unit_amount / 100 if price.unit_amount else 0
                    currency = price.currency.upper()
                    interval = (
                        price.recurring.interval if price.recurring else "one-time"
                    )

                    print(f"      • Price ID: {price.id}")
                    print(f"        Amount: {amount} {currency}/{interval}")
                    print(f"        Type: {price.type}")

                    # Suggest which env variable to use
                    product_name_lower = product.name.lower()
                    if "free" in product_name_lower or "trial" in product_name_lower:
                        print(f"        ➡️  Use for: STRIPE_FREE_PRICE_ID={price.id}")
                    elif (
                        "pro" in product_name_lower
                        and "enterprise" not in product_name_lower
                    ):
                        print(f"        ➡️  Use for: STRIPE_PRO_PRICE_ID={price.id}")
                    elif (
                        "enterprise" in product_name_lower
                        or "custom" in product_name_lower
                    ):
                        print(
                            f"        ➡️  Use for: STRIPE_ENTERPRISE_PRICE_ID={price.id}"
                        )

                    print()
            else:
                print("   ⚠️  No prices found for this product!")
                print("      Please create a price in Stripe Dashboard")

            print("-" * 80)
            print()

    print("\n📝 NEXT STEPS:")
    print("=" * 80)
    print("1. Copy the Price IDs (price_xxxxx) from above")
    print("2. Update your .env file with the correct Price IDs:")
    print("   STRIPE_FREE_PRICE_ID=price_xxxxx")
    print("   STRIPE_PRO_PRICE_ID=price_xxxxx")
    print("   STRIPE_ENTERPRISE_PRICE_ID=price_xxxxx")
    print("3. Restart your backend server")
    print("4. Test the subscription flow")
    print("=" * 80)

except stripe.error.AuthenticationError:
    print("❌ Authentication Error!")
    print("   Your STRIPE_SECRET_KEY is invalid or not set.")
    print("   Please check your .env file.")
except Exception as e:
    print(f"❌ Error: {str(e)}")
    print(f"   Type: {type(e).__name__}")
