export type OfferListItem = {
  id: number;
  offer_number: string;
  title: string | null;
  status: string;
  client_name: string;
  created_at: string | null;
  updated_at: string | null;
};

export type OfferLine = {
  id: number;
  position: number;
  description: string;
  quantity: number;
  unit: string;
  unit_price_eur: number;
  purchase_unit_price_eur: number | null;
  margin_percent: number;
  discount_percent: number;
  line_total_eur: number;
  product_id: number | null;
  supplier_id: number | null;
  supplier_name: string | null;
  supplier_code: string | null;
};

export type OfferDetail = {
  id: number;
  offer_number: string;
  title: string | null;
  status: string;
  valid_until: string | null;
  client_name: string;
  client_street: string | null;
  client_city: string | null;
  client_zip: string | null;
  client_country: string | null;
  client_ico: string | null;
  client_dic: string | null;
  client_ic_dph: string | null;
  client_contact: string | null;
  client_email: string | null;
  client_phone: string | null;
  notes_client: string | null;
  notes_internal: string | null;
  default_margin_percent: number;
  created_at: string | null;
  updated_at: string | null;
  lines: OfferLine[];
  subtotal_eur: number;
  vat_eur: number;
  total_eur: number;
};

export type CompanySettings = {
  company_name: string;
  street: string | null;
  city: string | null;
  zip_code: string | null;
  country: string | null;
  ico: string | null;
  dic: string | null;
  ic_dph: string | null;
  email: string | null;
  phone: string | null;
  web: string | null;
  iban: string | null;
  bank_name: string | null;
  logo_url: string | null;
  pdf_accent_color: string;
  offer_footer_note: string | null;
};
