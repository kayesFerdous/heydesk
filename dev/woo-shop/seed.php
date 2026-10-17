<?php
// The test products: a tupi on sale, and a panjabi with stock per size and colour.
update_option('woocommerce_currency', 'BDT');
update_option('woocommerce_manage_stock', 'yes');
$cat = wp_insert_term('Panjabi', 'product_cat');
$cat_id = is_wp_error($cat) ? $cat->get_error_data() : $cat['term_id'];

$p = new WC_Product_Simple();
$p->set_name('Cotton Tupi'); $p->set_sku('TP-1');
$p->set_regular_price('350'); $p->set_sale_price('300');
$p->set_manage_stock(true); $p->set_stock_quantity(12);
$p->set_description('<p>White cotton <b>tupi</b>.</p>');
$p->save();

$v = new WC_Product_Variable();
$v->set_name('Linen Panjabi'); $v->set_sku('LP-1'); $v->set_category_ids([$cat_id]);
$v->set_description('<p>Soft linen panjabi for Eid.</p>');
$attrs = [];
foreach (['Size' => ['M', 'L', 'XL'], 'Colour' => ['White', 'Black']] as $name => $opts) {
  $a = new WC_Product_Attribute(); $a->set_name($name); $a->set_options($opts);
  $a->set_visible(true); $a->set_variation(true); $attrs[] = $a;
}
$v->set_attributes($attrs); $id = $v->save();
foreach (['M-White' => 4, 'L-White' => 0, 'XL-White' => 2, 'M-Black' => 5, 'L-Black' => 3] as $key => $n) {
  [$size, $colour] = explode('-', $key);
  $var = new WC_Product_Variation(); $var->set_parent_id($id);
  $var->set_attributes(['size' => $size, 'colour' => $colour]);
  $var->set_regular_price('2450'); $var->set_manage_stock(true); $var->set_stock_quantity($n);
  $var->save();
}

// Cash on delivery and one delivery charge everywhere, so the website's own checkout works
update_option('woocommerce_cod_settings', ['enabled' => 'yes', 'title' => 'Cash on delivery',
  'description' => 'Pay when it arrives.', 'enable_for_methods' => [], 'enable_for_virtual' => 'yes']);
$zone = new WC_Shipping_Zone(0);  // "Locations not covered by your other zones": everywhere here
if (!$zone->get_shipping_methods()) {
  $method = $zone->add_shipping_method('flat_rate');
  update_option("woocommerce_flat_rate_{$method}_settings", ['title' => 'Delivery', 'cost' => '100', 'tax_status' => 'none']);
}

// Two API keys for HeyDesk: one Read/Write (what linking needs), one Read only (refused)
global $wpdb;
foreach (['read_write', 'read'] as $permissions) {
  $ck = 'ck_' . wc_rand_hash(); $cs = 'cs_' . wc_rand_hash();
  $wpdb->insert($wpdb->prefix . 'woocommerce_api_keys', [
    'user_id' => 1, 'description' => "HeyDesk $permissions", 'permissions' => $permissions,
    'consumer_key' => wc_api_hash($ck), 'consumer_secret' => $cs, 'truncated_key' => substr($ck, -7),
  ]);
  echo "$permissions key: $ck\n$permissions secret: $cs\n";
}
