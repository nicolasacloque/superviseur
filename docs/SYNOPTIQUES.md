# Créer un synoptique

Un synoptique est une page graphique (schéma d'installation, tableau de bord) dont les éléments, les
**widgets**, affichent des valeurs de points BACnet en direct, ou les commandent.

## 1. Ouvrir l'éditeur

Réservé aux comptes `engineer` et `admin`. Depuis la liste des synoptiques :
**Nouveau synoptique**, ou **Modifier** sur un synoptique existant. Les autres comptes consultent seulement
(un `operator` peut aussi commander les widgets `Commande` et `Consigne`).

Quatre zones : la **palette** des widgets (à gauche), le **canevas** au centre, les **propriétés** de la
sélection (à droite) et la barre d'état (avertissements, version enregistrée).

## 2. Composer la page

- **Ajouter** : cliquer un type dans la palette (le widget arrive au centre) ou le glisser sur le canevas.
- **Sélectionner** : clic ; Maj+clic pour ajouter ; rectangle de sélection sur le fond ; Ctrl+A pour tout.
- **Déplacer** : glisser, ou flèches (1 px, Maj : 10 px). **Redimensionner** : poignées de la sélection.
- **Grille** : « Magnétisme » aligne sur la grille (pas réglable dans les propriétés du synoptique) ;
  maintenir **Alt** le suspend pendant un geste.
- **Plusieurs widgets** : les propriétés proposent d'aligner (gauche, centre, droite, haut, milieu, bas) et de
  répartir régulièrement.
- **Copier / couper / coller / dupliquer** : Ctrl+C, X, V, D. **Premier plan / arrière-plan** : boutons du panneau.
- **Annuler / rétablir** : Ctrl+Z, Ctrl+Maj+Z (ou Ctrl+Y), 100 étapes.
- **Zoom** : − / + / Ajuster (pour travailler dans un grand canevas).
- **Canevas** (propriétés sans sélection) : nom, largeur et hauteur (1920×1080 par défaut), couleur de fond,
  image de fond (PNG, JPEG, WebP, SVG, 5 Mo au plus), pas de la grille.

## 3. Les widgets

| Widget | Sert à | Propriétés principales |
|---|---|---|
| **Valeur** | afficher un nombre avec son unité | point, libellé, format (`0.0`), afficher l'unité |
| **Texte** | titre ou étiquette fixe | texte |
| **Jauge** | position d'une valeur entre deux bornes | point, minimum, maximum, format |
| **Voyant** | état binaire ou multi-états (couleur + texte) | point, afficher l'état en texte |
| **Commande** | marche / arrêt d'un point binaire commandable | point commandé, libellé, priorité BACnet (1 à 16, 8 par défaut), confirmation |
| **Consigne** | saisie d'une valeur (bornes, pas), avec « Relâcher » | point commandé, minimum, maximum, pas, priorité, confirmation, bouton Relâcher |
| **Courbe** | tendance de 1 à 8 points (15 min à 30 jours), tableau alternatif | points, période initiale, sélecteur de période |
| **Alarmes** | liste des alarmes avec acquittement | filtre par chemin, états affichés, nombre de lignes, bouton d'acquittement |
| **Image** | logo, photo, schéma (importé dans le synoptique) | image, description, ajustement |
| **Lien** | navigation vers un autre synoptique | texte, synoptique cible |
| **Forme** | rectangle, ligne ou tuyau (avec écoulement animé) | forme, couleurs, épaisseur, point lié facultatif |

Pour lier un widget à un point : bouton **Choisir un point…**. Le sélecteur propose l'arborescence des
chemins (Site / Bâtiment / Étage / Équipement / Point) et une recherche par nom, chemin ou tag, avec la
valeur courante en aperçu.

Les widgets qui commandent (Commande, Consigne) ne sont actifs que pour les comptes `operator` et plus ;
chaque écriture est validée par l'API (bornes du point, rôle) et inscrite au journal d'audit.

## 4. Règles d'affichage

Chaque widget peut avoir des **règles** : quand la condition est vraie, un style s'applique. Toutes les
règles vraies s'appliquent, dans l'ordre de la liste : la dernière l'emporte en cas de conflit. Dans les
propriétés : **Ajouter une règle**, condition + style.

Conditions : `value` (la valeur), `status` (l'état : `ok`, `fault`, `overridden`, `out_of_service`, `stale`,
`comm_lost`), nombres, textes entre guillemets, `true`, `false`, `null`, et les opérateurs
`== != < <= > >=`, `&& ||` (ou `and` `or`), `!` (ou `not`), `+ - * / %`, parenthèses.

```
value > 28                          température trop haute
value > 28 && status == 'ok'        seulement si la mesure est saine
status != 'ok'                      point en défaut ou perdu
value == 1                          équipement en marche
```

Styles disponibles : `color`, `background`, `opacity`, `fontSize`, `fontWeight`, `textAlign`, `fill`, `stroke`,
`strokeWidth`, `borderColor`, `borderRadius`. Une condition invalide est refusée à l'enregistrement.
Ne jamais coder une alarme uniquement par la couleur : ajoutez un texte ou une forme.

## 5. Navigation entre synoptiques

- **Widget Lien** : bouton avec texte, qui ouvre le synoptique choisi.
- **Lien sur un widget** : dans la section « Navigation » des propriétés d'un widget, « Ouvre le synoptique »
  rend tout le widget cliquable (clic ou touche Entrée).

Le lien mémorise le **slug** du synoptique cible (`synoptic:<slug>`), stable même si le synoptique est
renommé. Un lien vers un synoptique supprimé mène à une page « introuvable ».

## 6. Aperçu, enregistrement, versions

- **Aperçu en direct** : affiche le synoptique tel que le verront les utilisateurs, avec les vraies valeurs,
  sans enregistrer. Échap ou « Retour à l'édition » pour revenir.
- **Enregistrer** (Ctrl+S) : crée une **nouvelle version**. Si quelqu'un d'autre a enregistré entre-temps,
  l'éditeur signale un conflit et n'écrase rien : rechargez la page puis reportez vos changements.
- **Versions** : liste des versions (date, auteur). **Restaurer** une version crée une nouvelle version
  identique à l'ancienne ; l'historique n'est jamais réécrit.
- Quitter avec des modifications non enregistrées demande confirmation.

## 7. Consulter un synoptique

Depuis la liste : clic sur le nom. Le synoptique s'adapte à la taille de l'écran (sans déformation) et se met
à jour en direct. Si la connexion temps réel est coupée, il est grisé jusqu'au retour ; les valeurs se
resynchronisent seules. Sur tablette en portrait, un synoptique 16/9 est réduit pour tenir en largeur :
prévoir une page dédiée (canevas 4/3 ou portrait) pour un usage principalement tablette.

## 8. Bonnes pratiques

- Un synoptique par équipement ou zone (une CTA, un étage) plutôt qu'un synoptique géant ; reliez-les par des liens.
- Pas plus de 500 widgets par synoptique ; préférez une courbe à plusieurs points à plusieurs courbes.
- Images légères (SVG pour les schémas) : elles sont stockées dans chaque version du synoptique.
- Testez les commandes sur un point non critique avant la mise en service, en tant qu'`operator`.
- Nommez les points de façon lisible dans le contrôleur ou via leur chemin logique : c'est ce que voient les
  utilisateurs dans les sélecteurs et les légendes.
