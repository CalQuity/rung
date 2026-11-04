export type Product = {
  id: number;
  slug: string;
  name: string;
  description: string;
  category: string;
  price_cents: number;
  emoji: string;
  color_from: string;
  color_to: string;
};

export type CartLine = {
  product: Product;
  quantity: number;
  line_total_cents: number;
};

export type Cart = {
  id: string;
  items: CartLine[];
  item_count: number;
  coupon: { code: string; description: string } | null;
  subtotal_cents: number;
  discount_cents: number;
  total_cents: number;
};

export type OrderLine = {
  product_id: number;
  name: string;
  emoji: string;
  unit_price_cents: number;
  quantity: number;
  line_total_cents: number;
};

export type Order = {
  number: string;
  status: string;
  customer_name: string;
  customer_email: string;
  coupon_code: string | null;
  items: OrderLine[];
  subtotal_cents: number;
  discount_cents: number;
  total_cents: number;
  payment: { charge_id: string; card_last4: string; status: string } | null;
  created_at: string;
};

export type CheckoutDetails = {
  name: string;
  email: string;
  card_number: string;
};
