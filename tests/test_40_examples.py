import pytest
from app.bot.conversation import (
    ChatState,
    ConversationManager,
    detect_missing_fields,
    extract_session_data,
    merge_session_data,
    create_document_payload,
)
from app.services.extractor import _fallback_extract_from_text
from app.services.validator import complete_and_validate
from app.services.generator_pdf import generate_pdf
from app.services.generator_docx import generate_docx
from app.storage import load_company_profile
from app.models import UNKNOWN

# 40 Diverse French BTP / Invoice Test Cases
TEST_CASES_40 = [
    # --- Category 1: Real-world Multi-line Trades (1-10) ---
    {
        "id": 1,
        "name": "Peinture & Moulures Multi-pièces",
        "input": """Devis pour Emmaüs, adresse 6 rue Archerau 75019 Paris
Sur les couloirs deux couches de satinée + pose des moulures 1600€ forfait
Sur les 3 Chambres peinture plafond et mur 2600€
Dans les WC deux couches de peinture 350€
Révision équipement logement 0€""",
        "expected_type": "devis",
        "expected_client": "Emmaüs",
        "expected_items_count": 4,
        "expected_ht": 4550.0,
    },
    {
        "id": 2,
        "name": "Électricité & Rénovation Complète",
        "input": """Facture pour SCI Voltaire, 12 boulevard Voltaire 75011 Paris
Remplacement tableau électrique aux normes NF 1850€
Création de 12 prises de courant et 6 points lumineux 1200€
Fourniture et pose chauffe-eau thermodynamique 2200€""",
        "expected_type": "facture",
        "expected_client": "SCI Voltaire",
        "expected_items_count": 3,
        "expected_ht": 5250.0,
    },
    {
        "id": 3,
        "name": "Plomberie Salle de Bain",
        "input": """Devis pour M. Jean Dupont, 14 rue de la Paix 75002 Paris
Dépose baignoire existante et évacuation gravats 450€
Fourniture et pose receveur douche italienne 1200€
Installation colonne de douche thermostatique 380€
Raccordement plomberie et évacuation PVC 550€""",
        "expected_type": "devis",
        "expected_client": "M. Jean Dupont",
        "expected_items_count": 4,
        "expected_ht": 2580.0,
    },
    {
        "id": 4,
        "name": "Menuiserie & Fenêtres PVC",
        "input": """Devis pour Cabinet Durand, 8 avenue Foch 75116 Paris
Fourniture et pose de 4 fenêtres double vitrage PVC 3200€
Dépose et reprise des dormants existants 600€
Pose volets roulants électriques aluminium 1800€""",
        "expected_type": "devis",
        "expected_client": "Cabinet Durand",
        "expected_items_count": 3,
        "expected_ht": 5600.0,
    },
    {
        "id": 5,
        "name": "Maçonnerie & Gros Œuvre",
        "input": """Facture pour SARL Horizon, adresse 45 rue Nationale 59000 Lille
Démolition mur porteur avec pose IPN acier 3400€
Évacuation des gravats en déchetterie agréée 800€
Coulage chape liquide fibrée 1500€""",
        "expected_type": "facture",
        "expected_client": "SARL Horizon",
        "expected_items_count": 3,
        "expected_ht": 5700.0,
    },
    {
        "id": 6,
        "name": "Carrelage & Faïence",
        "input": """Devis pour Mme Sophie Bernard, 22 allée des Érables 69003 Lyon
Préparation sol primaire d'accrochage et ragréage 650€
Pose carrelage grand format 60x60 45 m2 1800€
Fourniture et pose faïence murale salle d'eau 950€
Jointoiement hydrofuge et finitions 350€""",
        "expected_type": "devis",
        "expected_client": "Mme Sophie Bernard",
        "expected_items_count": 4,
        "expected_ht": 3750.0,
    },
    {
        "id": 7,
        "name": "Couverture & Toiture",
        "input": """Devis pour M. Robert Leroy, 5 route de Saint-Germain 78000 Versailles
Remplacement tuiles cassées et remaniement toiture 1900€
Nettoyage haute pression et traitement hydrofuge 1100€
Pose gouttières zinc demi-ronde 25 ml 850€""",
        "expected_type": "devis",
        "expected_client": "M. Robert Leroy",
        "expected_items_count": 3,
        "expected_ht": 3850.0,
    },
    {
        "id": 8,
        "name": "Isolation Thermique & Plâtrerie",
        "input": """Devis pour M. et Mme Laurent, 30 rue Gambetta 92100 Boulogne
Isolation combles perdus laine de roche soufflée 2100€
Pose faux plafond plâtre BA13 sur ossature métallique 1400€
Bande à joint 3 passes et ponçage soigné 750€""",
        "expected_type": "devis",
        "expected_client": "M. et Mme Laurent",
        "expected_items_count": 3,
        "expected_ht": 4250.0,
    },
    {
        "id": 9,
        "name": "Parquet & Sols Souples",
        "input": """Facture pour Agence Nexia, 15 rue du Temple 75003 Paris
Arrachage moquette ancienne et ponçage sol 500€
Pose parquet contrecollé chêne naturel 50 m2 2500€
Pose plinthes assorties en périphérie 450€""",
        "expected_type": "facture",
        "expected_client": "Agence Nexia",
        "expected_items_count": 3,
        "expected_ht": 3450.0,
    },
    {
        "id": 10,
        "name": "Cuisine Équipée & Agencement",
        "input": """Devis pour M. Thomas Roux, 3 rue Pasteur 94200 Ivry-sur-Seine
Montage et fixation meubles de cuisine haut et bas 1300€
Découpe et pose plan de travail sur-mesure 600€
Raccordement évier, mitigeur et électroménager 450€""",
        "expected_type": "devis",
        "expected_client": "M. Thomas Roux",
        "expected_items_count": 3,
        "expected_ht": 2350.0,
    },

    # --- Category 2: Single Prestation Quotes & Fast Inputs (11-20) ---
    {
        "id": 11,
        "name": "Peinture Studio express",
        "input": "Devis pour Karim Ben Salah, adresse 15 rue Victor Hugo Paris, peinture studio 950€ HT, TVA 10%",
        "expected_type": "devis",
        "expected_client": "Karim Ben Salah",
        "expected_items_count": 1,
        "expected_ht": 950.0,
    },
    {
        "id": 12,
        "name": "Dépannage plomberie urgent",
        "input": "Facture pour Mme Claire Valette, adresse 4 passage des Panoramas 75002 Paris, remplacement ballon eau chaude 680€",
        "expected_type": "facture",
        "expected_client": "Mme Claire Valette",
        "expected_items_count": 1,
        "expected_ht": 680.0,
    },
    {
        "id": 13,
        "name": "Pose parquet simple",
        "input": "Devis pour Paul Martin, adresse 18 rue de Rennes 75006 Paris, pose de parquet flottant 1200€",
        "expected_type": "devis",
        "expected_client": "Paul Martin",
        "expected_items_count": 1,
        "expected_ht": 1200.0,
    },
    {
        "id": 14,
        "name": "Installation Climatisation Réversible",
        "input": "Devis pour Société BatiTech, adresse 120 avenue Jean Jaurès 69007 Lyon, climatisation bi-split Daikin 3400€",
        "expected_type": "devis",
        "expected_client": "Société BatiTech",
        "expected_items_count": 1,
        "expected_ht": 3400.0,
    },
    {
        "id": 15,
        "name": "Ponçage et Vitrification parquet",
        "input": "Facture pour Marc Lefebvre, adresse 8 rue Monge 75005 Paris, ponçage vitrification parquet 3 couches 1650€",
        "expected_type": "facture",
        "expected_client": "Marc Lefebvre",
        "expected_items_count": 1,
        "expected_ht": 1650.0,
    },
    {
        "id": 16,
        "name": "Serrurerie & Porte Blindée",
        "input": "Facture pour Julie Meunier, adresse 55 rue de Vaugirard 75006 Paris, installation bloc-porte blindée A2P 2100€",
        "expected_type": "facture",
        "expected_client": "Julie Meunier",
        "expected_items_count": 1,
        "expected_ht": 2100.0,
    },
    {
        "id": 17,
        "name": "Ravalement façade partielle",
        "input": "Devis pour Copropriété Le Belvédère, adresse 14 rue de Belfort 25000 Besançon, nettoyage et enduit façade 4800€",
        "expected_type": "devis",
        "expected_client": "Copropriété Le Belvédère",
        "expected_items_count": 1,
        "expected_ht": 4800.0,
    },
    {
        "id": 18,
        "name": "Création dressing sur mesure",
        "input": "Devis pour Alain Delon, adresse 2 rue de Rivoli 75004 Paris, fabrication et pose dressing penderie 1750€",
        "expected_type": "devis",
        "expected_client": "Alain Delon",
        "expected_items_count": 1,
        "expected_ht": 1750.0,
    },
    {
        "id": 19,
        "name": "Étanchéité terrasse balcon",
        "input": "Facture pour Sandrine Petit, adresse 9 rue Saint-Charles 75015 Paris, réfection étanchéité résine 1350€",
        "expected_type": "facture",
        "expected_client": "Sandrine Petit",
        "expected_items_count": 1,
        "expected_ht": 1350.0,
    },
    {
        "id": 20,
        "name": "Remplacement VMC collective",
        "input": "Facture pour Syndic Gestrim, adresse 10 place Bellecour 69002 Lyon, groupe VMC autoréglable 890€",
        "expected_type": "facture",
        "expected_client": "Syndic Gestrim",
        "expected_items_count": 1,
        "expected_ht": 890.0,
    },

    # --- Category 3: Units, Discounts, Deposits & Rates (21-28) ---
    {
        "id": 21,
        "name": "M2 et Prix unitaire",
        "input": "Devis pour Société Bâtiment Moderne, adresse 10 rue Lafayette Paris, pose carrelage 45 m2, prix unitaire HT 35€/m2, TVA 20%",
        "expected_type": "devis",
        "expected_client": "Société Bâtiment Moderne",
        "expected_items_count": 1,
        "expected_ht": 1575.0,  # 45 * 35 = 1575
    },
    {
        "id": 22,
        "name": "Avec Remise commerciale",
        "input": """Devis pour M. Philippe Noel, adresse 3 rue de Birague 75004 Paris
Peinture cage d'escalier 2500€
remise 200€""",
        "expected_type": "devis",
        "expected_client": "M. Philippe Noel",
        "expected_items_count": 1,
        "expected_ht": 2300.0,  # 2500 - 200
    },
    {
        "id": 23,
        "name": "Avec Acompte versé",
        "input": """Facture pour Denis Girard, adresse 7 rue Oberkampf 75011 Paris
Rénovation cuisine 4000€
acompte 1000€""",
        "expected_type": "facture",
        "expected_client": "Denis Girard",
        "expected_items_count": 1,
        "expected_ht": 4000.0,
    },
    {
        "id": 24,
        "name": "TVA rénovation énergétique 5.5%",
        "input": "Facture pour Roger Blanc, adresse 25 rue Carnot 93100 Montreuil, pose pompe à chaleur Air/Eau 7500€, TVA 5.5%",
        "expected_type": "facture",
        "expected_client": "Roger Blanc",
        "expected_items_count": 1,
        "expected_ht": 7500.0,
    },
    {
        "id": 25,
        "name": "TVA 10% Travaux d'amélioration",
        "input": "Devis pour Catherine Marchand, adresse 11 rue de Tolbiac 75013 Paris, rénovation plomberie sanitaires 1900€, TVA 10%",
        "expected_type": "devis",
        "expected_client": "Catherine Marchand",
        "expected_items_count": 1,
        "expected_ht": 1900.0,
    },
    {
        "id": 26,
        "name": "Décimales et centimes",
        "input": "Devis pour Bureau Conseil, adresse 100 rue de Courcelles 75017 Paris, câblage réseau RJ45 1250.50€",
        "expected_type": "devis",
        "expected_client": "Bureau Conseil",
        "expected_items_count": 1,
        "expected_ht": 1250.50,
    },
    {
        "id": 27,
        "name": "Prix écrit en 'euros HT'",
        "input": "Devis pour David Cohen, adresse 17 avenue Trudaine 75009 Paris, rafraîchissement peintures 1800 euros HT",
        "expected_type": "devis",
        "expected_client": "David Cohen",
        "expected_items_count": 1,
        "expected_ht": 1800.0,
    },
    {
        "id": 28,
        "name": "Prestation gratuite (Option 0€)",
        "input": """Devis pour M. Eric Morel, adresse 19 rue de Cléry 75002 Paris
Pose de faux plafond suspendu 1400€
Nettoyage et évacuation de fin de chantier 0€""",
        "expected_type": "devis",
        "expected_client": "M. Eric Morel",
        "expected_items_count": 2,
        "expected_ht": 1400.0,
    },

    # --- Category 4: Addresses, Chantier & Client variations (29-34) ---
    {
        "id": 29,
        "name": "Chantier différent du client",
        "input": """Devis pour SCI Montmartre, adresse client 10 rue Lafayette 75009 Paris, chantier 15 rue Caulaincourt 75018 Paris
Rénovation complète appartement T2 6800€""",
        "expected_type": "devis",
        "expected_client": "SCI Montmartre",
        "expected_items_count": 1,
        "expected_ht": 6800.0,
    },
    {
        "id": 30,
        "name": "Adresse avec 'chantier même adresse'",
        "input": """Devis pour Lucie Bernard, adresse 45 rue de la Liberté 59800 Lille, chantier même adresse
Ponçage et peinture escalier bois 1150€""",
        "expected_type": "devis",
        "expected_client": "Lucie Bernard",
        "expected_items_count": 1,
        "expected_ht": 1150.0,
    },
    {
        "id": 31,
        "name": "Client Entreprise SAS",
        "input": "Facture pour SAS BatiRenov Pro, adresse 84 route de Lyon 38000 Grenoble, location échafaudage et pose 2200€",
        "expected_type": "facture",
        "expected_client": "SAS BatiRenov Pro",
        "expected_items_count": 1,
        "expected_ht": 2200.0,
    },
    {
        "id": 32,
        "name": "Adresse province longue",
        "input": "Devis pour M. Gérard Philippe, adresse 12 chemin des Vignes Hameau de l'Église 33000 Bordeaux, réfection toiture grange 3900€",
        "expected_type": "devis",
        "expected_client": "M. Gérard Philippe",
        "expected_items_count": 1,
        "expected_ht": 3900.0,
    },
    {
        "id": 33,
        "name": "Client nommé avec 'Client: ...'",
        "input": "Devis Client: Hôtel Le Parisien, adresse 25 rue de Châteaudun 75009 Paris, remise aux normes éclairage de sécurité 1450€",
        "expected_type": "devis",
        "expected_client": "Hôtel Le Parisien",
        "expected_items_count": 1,
        "expected_ht": 1450.0,
    },
    {
        "id": 34,
        "name": "Adresse avec numéro, bis et lieu-dit",
        "input": "Facture pour François Villon, adresse 14 bis rue du Moulin 77000 Melun, installation interphone vidéo 780€",
        "expected_type": "facture",
        "expected_client": "François Villon",
        "expected_items_count": 1,
        "expected_ht": 780.0,
    },

    # --- Category 5: Real-world Artisan Styles & Formatting (35-40) ---
    {
        "id": 35,
        "name": "Artisan avec puces '-' et lots",
        "input": """Devis pour M. Vincent Cassel, adresse 30 rue du Bac 75007 Paris
- Dépose cuisine existante 400€
- Réfection plomberie complète 1200€
- Pose nouvelle cuisine 1800€
- Peinture murs et plafonds 900€""",
        "expected_type": "devis",
        "expected_client": "M. Vincent Cassel",
        "expected_items_count": 4,
        "expected_ht": 4300.0,
    },
    {
        "id": 36,
        "name": "Texte avec numéros dans la prestation",
        "input": """Devis pour Mme Anne Hidalgo, adresse 1 place de l'Hôtel de Ville 75004 Paris
Démontage de 5 radiateurs en fonte et purge 850€
Pose de 5 nouveaux radiateurs basse consommation 1750€""",
        "expected_type": "devis",
        "expected_client": "Mme Anne Hidalgo",
        "expected_items_count": 2,
        "expected_ht": 2600.0,
    },
    {
        "id": 37,
        "name": "Format 'forfait' explicite",
        "input": """Devis pour Guillaume Canet, adresse 16 rue Saint-Denis 75001 Paris
Forfait déplacement et recherche de fuite d'eau 250€
Forfait réparation canalisation cuivre 380€""",
        "expected_type": "devis",
        "expected_client": "Guillaume Canet",
        "expected_items_count": 2,
        "expected_ht": 630.0,
    },
    {
        "id": 38,
        "name": "Prix avec espaces '1 600 €'",
        "input": "Facture pour Sophie Marceau, adresse 20 rue de Bellechasse 75007 Paris, rénovation véranda aluminium 2 500€",
        "expected_type": "facture",
        "expected_client": "Sophie Marceau",
        "expected_items_count": 1,
        "expected_ht": 2500.0,
    },
    {
        "id": 39,
        "name": "Mention du mode de paiement virement",
        "input": """Devis pour Romain Duris, adresse 44 rue de Charonne 75011 Paris, pose verrière type atelier 1550€, paiement par virement""",
        "expected_type": "devis",
        "expected_client": "Romain Duris",
        "expected_items_count": 1,
        "expected_ht": 1550.0,
    },
    {
        "id": 40,
        "name": "Gros chantier avec 5 lots complets",
        "input": """Devis pour Association Aurore, adresse 10 rue de Meaux 75019 Paris
Lot 1 Maçonnerie démolition cloisons plâtre 1200€
Lot 2 Électricité réfection générale T3 2400€
Lot 3 Plomberie remplacement chauffe eau et évacuations 1600€
Lot 4 Peintures murs plafonds satinée 2800€
Lot 5 Nettoyage désinfection fin de travaux 500€""",
        "expected_type": "devis",
        "expected_client": "Association Aurore",
        "expected_items_count": 5,
        "expected_ht": 8500.0,
    },
]


def test_40_examples_battery():
    comp = load_company_profile()
    failures = []
    successes = []

    for case in TEST_CASES_40:
        cid = case["id"]
        cname = case["name"]
        raw_text = case["input"]

        # Run extraction through ConversationManager / local fallback
        state = ChatState()
        data = extract_session_data(raw_text, state)
        merge_session_data(state, data)
        missing = detect_missing_fields(state)

        # Check document type
        doc_type = state.data.get("document_type")
        if doc_type != case["expected_type"]:
            failures.append(f"Case {cid} ({cname}): expected type {case['expected_type']}, got {doc_type}")
            continue

        # Check client name
        client_name = state.data.get("client_name") or ""
        expected_client = case["expected_client"]
        if expected_client.lower() not in client_name.lower():
            failures.append(f"Case {cid} ({cname}): expected client '{expected_client}', got '{client_name}'")
            continue

        # Check missing fields (should be empty when client and address are provided)
        if missing:
            failures.append(f"Case {cid} ({cname}): unexpected missing fields {missing}")
            continue

        # Check items count and HT total
        items = state.data.get("items") or []
        if len(items) != case["expected_items_count"]:
            failures.append(f"Case {cid} ({cname}): expected {case['expected_items_count']} items, got {len(items)}")
            continue

        # Check payload generation & fallback extraction
        payload = create_document_payload(state)
        extracted_doc = _fallback_extract_from_text(payload)
        completed_doc, corrections, val_missing = complete_and_validate(extracted_doc)

        calc_ht = completed_doc.totals.subtotal_ht
        expected_ht = case["expected_ht"]
        if abs((calc_ht or 0) - expected_ht) > 0.05:
            failures.append(f"Case {cid} ({cname}): expected HT {expected_ht}, got {calc_ht}")
            continue

        successes.append(f"Case {cid} ({cname}): PASSED (HT: {calc_ht} EUR, TTC: {completed_doc.totals.total_ttc} EUR)")

    print(f"\n===== RESULTS: {len(successes)}/40 PASSED =====")
    for s in successes:
        print("  [OK]", s)
    if failures:
        print(f"\n===== FAILURES ({len(failures)}/40): =====")
        for f in failures:
            print("  [FAIL]", f)

    assert len(failures) == 0, f"{len(failures)} test cases failed out of 40:\n" + "\n".join(failures)
