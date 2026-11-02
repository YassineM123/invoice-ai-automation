from app.bot.conversation import parse_items_from_text, extract_session_data, detect_missing_fields, ChatState, merge_session_data

# Test 1: multi-line forfait messages from phone screenshot
text = (
    "Sur les couloirs sur les plafond et mur deux couches de satinee + pose des moulures 1600 forfait\n"
    "Sur les 3 Chambres plafond et mur peinture plafond et mur deux couches mate et sur les murs deux couches satines + nettoyage moquette + demontage de 3 radiateurs 2600\n"
    "Dans les WC deux couches de peinture + pose de moulures 350\n"
    "Revision equipement logement 0 forfait\n"
    "Lot electricite\n"
    "Fourniture et pose de spot + mise en securite de logement 450\n"
    "Sur les boiseries\n"
    "Porte plinthe fenetre face interieur\n"
    "Peinture sur ouvrage bois 450"
)

items = parse_items_from_text(text)
print(f"=== Items found: {len(items)} ===")
for it in items:
    desc = it["description"][:70]
    print(f"  - {desc} | qty={it['quantity']} | price={it['unit_price_ht']}")

print()

# Test 2: single line with address + service
text2 = "Devis pour Societe Batiment Moderne, adresse 10 rue Lafayette Paris, pose de carrelage 45 m2, prix unitaire HT 35/m2, TVA 20%"
data = extract_session_data(text2)
print("=== Single-line test ===")
print(f"client_name: {data.get('client_name')}")
print(f"client_address: {data.get('client_address')}")
print(f"worksite_address: {data.get('worksite_address')}")
print(f"items: {data.get('items')}")

print()

# Test 3: missing fields detection with items
state = ChatState()
state.data["document_type"] = "devis"
state.data["client_name"] = "Societe Batiment Moderne"
state.data["client_address"] = "10 rue Lafayette Paris"
state.data["worksite_address"] = "15 rue Victor Hugo 75001 Paris"
state.data["items"] = [{"description": "Pose carrelage", "quantity": 1.0, "unit_price_ht": 1600.0, "vat_rate": 20.0}]
state.missing_fields = detect_missing_fields(state)
print(f"=== Missing fields with items ===")
print(f"Missing: {state.missing_fields}")

