<div align="center">

# Awtrix Studio

**Affichez tout ce que vous voulez de Home Assistant sur votre horloge AWTRIX, et laissez-la se mettre à jour toute seule.**
Sans automatisation, sans blueprint, sans YAML.
Compatible **AWTRIX 3** et **AWTRIX NG** : Ulanzi TC001, Ulanzi TC002 et horloges DIY.

[![Ouvrir dans HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=3615nulsi&repository=ha-awtrix-studio&category=integration)

[![Validate](https://github.com/3615nulsi/ha-awtrix-studio/actions/workflows/validate.yml/badge.svg)](https://github.com/3615nulsi/ha-awtrix-studio/actions/workflows/validate.yml)
[![Tests](https://github.com/3615nulsi/ha-awtrix-studio/actions/workflows/tests.yml/badge.svg)](https://github.com/3615nulsi/ha-awtrix-studio/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

🇺🇸 [Read in English](README.md)

![Le panneau Awtrix Studio](https://raw.githubusercontent.com/3615nulsi/ha-awtrix-studio/main/docs/images/overview-fr.png)

</div>

## ✨ Ce que ça vous apporte

- **Un panneau dans la barre latérale** qui montre ce que l'horloge affiche en ce moment, chaque app avec sa vignette en direct, et un interrupteur pour activer ou désactiver chacune.
- **Des apps en quelques clics.** Choisissez un capteur, une icône et une couleur. Vous voulez une couleur qui glisse du vert au rouge quand le CO2 monte ? Choisissez un dégradé. Un niveau de batterie ? Ajoutez une barre de progression.
- **Toutes les icônes LaMetric, rien à télécharger.** Cherchez dans la galerie LaMetric directement depuis l'éditeur : Awtrix Studio envoie l'icône avec l'app, plus besoin de charger les icônes sur l'horloge au préalable.
- **Un aperçu en direct pendant l'édition**, et un bouton pour essayer l'app sur la vraie horloge avant d'enregistrer.
- **Plusieurs pages par app** qui alternent toutes seules, par exemple « prochain tram » puis « le suivant ».
- **Les trois indicateurs colorés** de l'horloge, pilotés de la même façon.
- **Un Home Assistant en meilleure santé.** Les apps ne sont mises à jour que quand leurs données changent, et ne laissent rien dans la base d'historique. L'approche habituelle, « une automatisation toutes les quelques secondes », peut écrire des centaines de milliers d'entrées par jour.

## ⏰ Horloges prises en charge

Awtrix Studio pilote les horloges sous l'un ou l'autre firmware, et les reconnaît tout seul quand vous ajoutez une horloge.

| | AWTRIX 3 | AWTRIX NG |
|---|---|---|
| **Horloges** | Ulanzi TC001, horloges DIY à ESP32 | Ulanzi TC002, et TC001 ou horloges DIY à ESP32 flashées avec NG |
| **Écran** | 32 × 8 | 32 × 8 ; sur le TC002, 52 × 16 : les apps sont dessinées en double taille sur une grille de 26 × 8, ou au pixel près avec une **mise en page** |
| **Apps simples, indicateurs, options d'affichage** | ✅ | ✅ écrits pour vous dans le JSON propre à NG |
| **Mode modèle** | JSON AWTRIX 3 | JSON AWTRIX NG ; les clés refusées par l'horloge sont affichées dans le panneau |
| **Icônes LaMetric animées** | déposées sur l'horloge par sa page web | déposées sur l'horloge par son API de fichiers, ou envoyées avec l'app (NG 1.2 ou plus récent) |
| **Icônes présentes sur l'horloge** | affichées dans le panneau | affichées dans le panneau, les 16 × 16 en taille réelle sur le TC002 |
| **Ordre des apps** | réglé par la page web de l'horloge, rétabli quand une app revient | mémorisé par l'horloge elle-même |
| **Absent de ce firmware** | – | texte en haut, conservation d'une app après un redémarrage |

## 🚀 Démarrer en 3 étapes

Il vous faut Home Assistant 2025.8 ou plus récent, l'[intégration MQTT](https://www.home-assistant.io/integrations/mqtt/), et une horloge sous [AWTRIX 3](https://blueforcer.github.io/awtrix3/) ou [AWTRIX NG](https://blueforcer.github.io/awtrix-ng/), reliée au même broker MQTT. Sous AWTRIX NG, MQTT reste désactivé tant que vous ne l'activez pas dans la page web de l'horloge (**System → MQTT**).

1. **Installer.** Cliquez sur le bouton **Ouvrir dans HACS** ci-dessus, puis **Télécharger**, et redémarrez Home Assistant.
   <details><summary>Sans le bouton</summary>

   Dans HACS, ouvrez le menu ⋮ → **Dépôts personnalisés**, ajoutez `https://github.com/3615nulsi/ha-awtrix-studio` en tant qu'**Intégration**, puis cherchez **Awtrix Studio** et téléchargez-la. Ou copiez à la main `custom_components/awtrix_studio` dans votre dossier de configuration.
   </details>

2. **Ajouter l'horloge.** Allez dans **Paramètres → Appareils et services → Ajouter une intégration → Awtrix Studio**, et saisissez le préfixe MQTT de l'horloge (il figure dans ses réglages MQTT, par exemple `awtrix_1f1b94`). Laissez le firmware sur **Détecter automatiquement** : Awtrix Studio interroge l'horloge, qui doit être allumée et connectée à MQTT.

3. **Créer votre première app.** Ouvrez **Awtrix Studio** dans la barre latérale, cliquez sur **Ajouter une app**, choisissez un capteur… et regardez-le apparaître sur l'horloge. 🎉

## 🖼️ Petit tour du propriétaire

### La vue d'ensemble

Le haut du panneau reproduit l'écran réel de l'horloge. En dessous, chaque app a sa carte avec ce qu'elle affiche et où elle en est : à l'écran, dans la rotation, masquée faute de contenu, ou désactivée.

### Le mode simple : zéro code

Choisissez une entité, ajoutez un libellé, une icône et des couleurs, et c'est fini. L'aperçu suit ce que vous tapez.

![Éditeur en mode simple](https://raw.githubusercontent.com/3615nulsi/ha-awtrix-studio/main/docs/images/simple-fr.png)

| Réglage | À quoi ça sert |
|---|---|
| **Entité** | Le capteur (ou n'importe quelle entité) à afficher. |
| **Libellé** | Texte facultatif avant la valeur, par exemple `CO2`. |
| **Icône** | **Choisir une icône** cherche dans la [galerie LaMetric](https://developer.lametric.com/icons) ; Awtrix Studio l'envoie avec l'app (les icônes animées sont déposées automatiquement sur l'horloge, pour être animées). Saisir le numéro d'une icône fonctionne aussi, et un fichier de l'horloge portant ce nom reste prioritaire. Vous pouvez aussi saisir le nom d'une icône présente sur l'horloge. |
| **Couleur du texte** | La couleur par défaut. |
| **Décimales** | Arrondit la valeur ; laissez vide pour l'afficher telle quelle. |
| **Afficher l'unité** | Ajoute `°C`, `%`, `kW`… après la valeur. |
| **Masquer quand la valeur vaut 0** | Pratique pour la production solaire la nuit. |
| **Barre de progression** | Une barre en bas de l'écran, à partir de l'entité de l'app ou d'une autre, vide et pleine aux valeurs de votre choix. Elle peut suivre la couleur du texte. |
| **Ajouter une entité** | Affiche plusieurs entités sur la même ligne, par exemple `UP 750 DN 30`. Chacune a son libellé, ses décimales, son unité et sa couleur (fixe, par paliers ou en dégradé) ; un onglet par entité permet de passer de l'une à l'autre, et un **séparateur** se place entre deux. Une entité sans valeur quitte la ligne, et l'app quitte la rotation quand il n'en reste aucune. Sur AWTRIX NG, toute la ligne prend la couleur de la première entité. |
| **Mise en page** (TC002 sous AWTRIX NG) | Dessine l'app sur les 52 × 16 LED, une LED par pixel, au lieu d'une ligne en double taille : **Valeur**, **Titre et valeur** (le libellé en petit au-dessus), **Valeur et barre de progression**, ou **Deux entités, une par ligne**. L'icône est facultative ; une icône de la galerie y est dessinée en 16 × 16. Vous choisissez les polices parmi celles qui restent lisibles : pour la valeur, une police de 8 pixels (épaisse par défaut, fine, ou large pour une valeur courte), et pour le titre ou les lignes, une police de 6 pixels (fine par défaut, ou épaisse). |
| **Couleur selon la valeur** | **Fixe**, par **paliers** (jusqu'à deux seuils, chacun avec sa couleur), ou en **dégradé** : choisissez-en un tout prêt (*plus c'est haut, pire c'est*, *plus c'est bas, pire c'est*, *idéal au milieu*, *froid → chaud*) et réglez ses bornes, ou créez le vôtre avec 2 à 5 points. Pour une température, du CO2, de l'humidité ou une batterie, des bornes adaptées sont proposées. |

L'app quitte aussi la rotation d'elle-même tant que son entité est indisponible.

### Le mode modèle : liberté totale

Pour aller plus loin (dessins, barres de progression, plusieurs pages), écrivez un modèle Home Assistant qui produit le JSON de l'horloge. L'aperçu montre chaque page, les fait alterner comme l'horloge, et signale tout de suite ce qui cloche.

![Éditeur en mode modèle](https://raw.githubusercontent.com/3615nulsi/ha-awtrix-studio/main/docs/images/editor-fr.png)

Vous avez commencé en mode simple et voulez aller plus loin ? **Convertir en mode modèle** ouvre le modèle généré, prêt à être enrichi.

### Les options d'affichage

Chaque app, dans les deux modes, a une section **Affichage** pour le reste des réglages d'une app AWTRIX : durée d'affichage, nombre de défilements d'un texte long, vitesse de défilement, comportement de l'icône pendant le défilement, majuscules, centrage, texte en haut, texte arc-en-ciel ou en dégradé de deux couleurs, texte clignotant ou en fondu, couleur de fond, l'un des 19 effets de fond de l'horloge (avec sa vitesse, sa palette et le fondu des couleurs), une météo par-dessus (neige, pluie, orage…), et la conservation de l'app par l'horloge après un redémarrage. Un réglage laissé tel quel suit celui de l'horloge. En mode modèle, ce sont des valeurs par défaut : une clé fixée par votre modèle reste prioritaire. Sous AWTRIX NG, les effets, météos et palettes proposés sont ceux que l'horloge annonce, la vitesse de l'effet est un multiplicateur (2 va deux fois plus vite), et le texte arc-en-ciel ou en dégradé partage avec l'effet de fond l'unique palette de l'app.

### L'ordre des apps

**Ordre** liste toute la boucle de l'horloge, y compris ses apps natives (heure, date, température…) et celles envoyées par d'autres outils. Faites glisser les apps ou utilisez les flèches, puis enregistrez. L'horloge suit ensuite cet ordre : une app qui revient après avoir été masquée reprend sa place au lieu de finir en bout de boucle.

AWTRIX 3 ne permet de réordonner les apps que par sa page web : Awtrix Studio utilise donc l'adresse que l'horloge annonce en MQTT, comme pour les icônes animées. Si la page web n'est pas joignable (ou protégée par mot de passe), l'ordre est conservé et appliqué dès que possible. AWTRIX NG mémorise l'ordre lui-même : il est envoyé une fois, et les apps que vous avez désactivées sur l'horloge le restent.

### Les indicateurs

L'horloge a trois petits voyants colorés sur le bord droit de l'écran. Allumez-en un quand une porte est ouverte, affichez votre surplus solaire en couleur, signalez un nouveau courrier… En mode simple, c'est soit « allumé de cette couleur tant que cette entité est dans cet état », soit « toujours allumé, d'une couleur qui suit cette valeur », avec les mêmes dégradés que les apps. Clignotement ou fondu en option.

### Partager et copier des apps

**Exporter** enregistre les apps et indicateurs choisis dans un petit fichier JSON, ou les copie pour les coller dans un message de forum. **Importer** lit un tel fichier sur une autre horloge (ou un autre Home Assistant) et montre exactement ce qui va se passer avant de modifier quoi que ce soit : un nom déjà pris reçoit un nom libre ou peut remplacer l'app existante, les entités absentes de cette installation sont listées, et les modèles peuvent être lus avant d'être acceptés.

Les apps simples décrivent *quoi* afficher, pas comment un firmware le dessine : elles passent donc d'AWTRIX 3 à AWTRIX NG et inversement. Les apps en mode modèle contiennent le JSON du firmware de leur horloge, et sont signalées si on les importe sur une horloge sous l'autre firmware.

## 📝 Exemples de modèles

Ces exemples sont écrits pour AWTRIX 3. Sous AWTRIX NG, quelques clés changent : `color` devient `textColor`, `blinkText` devient `textBlinkMs`, `"center": true` devient `"textAlign": "center"`, `progressC` devient `progressColor`, et un dessin est un tableau comme `["pixel", 20, 7, "#EF7D00"]` au lieu de `{"dp": [20, 7, "#EF7D00"]}`. Tous les changements sont listés dans le guide de NG [Coming from AWTRIX 3](https://github.com/Blueforcer/awtrix-ng/blob/main/docs/guides/migrating-from-awtrix3.md).

<details>
<summary><b>Une seule page</b></summary>

```jinja
{"text": "{{ states('sensor.temperature_exterieure') | round(1) }}°", "icon": "2355"}
```
</details>

<details>
<summary><b>Plusieurs pages qui alternent</b></summary>

Produisez une liste : chaque élément est une page.

```jinja
{{ [
  {"text": states('sensor.temperature_exterieure') ~ "°", "icon": "2355"},
  {"text": states('sensor.humidite_exterieure') ~ "%", "icon": "2423"}
] | tojson }}
```
</details>

<details>
<summary><b>Seulement quand il y a quelque chose à afficher</b></summary>

Produisez `[]` et l'app quitte la rotation jusqu'à ce qu'il y ait de nouveau quelque chose.

```jinja
{% set n = states('sensor.mails_non_lus') | int(0) %}
{{ ([{"text": n ~ " mails", "icon": "51"}] if n > 0 else []) | tojson }}
```
</details>

<details>
<summary><b>Les deux prochains trams, avec un repère dessiné au pixel</b></summary>

Affiche le prochain départ, puis le suivant, avec un ou deux points en bas pour les distinguer. Les départs viennent d'un capteur comme celui de [ha-tam-montpellier](https://github.com/3615nulsi/ha-tam-montpellier).

```jinja
{%- set deps = state_attr('sensor.mon_arret_prochain_tram', 'departures') or [] -%}
{%- set ns = namespace(pages=[]) -%}
{%- for d in deps[:2] -%}
  {%- set m = d.minutes | int -%}
  {%- set dots = [{"dp": [20, 7, "#EF7D00"]}] if loop.first
       else [{"dp": [19, 7, "#EF7D00"]}, {"dp": [21, 7, "#EF7D00"]}] -%}
  {%- set page = {"center": true, "draw": dots} -%}
  {%- if m == 0 -%}
    {%- set page = dict(page, text="Quai", blinkText=500) -%}
  {%- else -%}
    {%- set page = dict(page, text=m ~ " min") -%}
  {%- endif -%}
  {%- set ns.pages = ns.pages + [page] -%}
{%- endfor -%}
{{ ns.pages | tojson }}
```
</details>

<details>
<summary><b>Un indicateur pour le surplus solaire</b></summary>

Bleu quand vous injectez, du vert au rouge quand vous consommez sur le réseau. Le mode simple s'en approche avec un dégradé personnalisé (bleu à -2 kW, vert à 0, jaune à 0,5, rouge à 3) ; ce modèle permet un réglage plus fin.

```jinja
{%- set w = states('sensor.puissance_reseau') | float(0) * 1000 -%}
{%- if w < 0 -%}
  {"color": [0, 150, {{ (150 + [w | abs, 2000] | min / 2000 * 105) | int }}]}
{%- elif w <= 500 -%}
  {"color": [{{ (w / 500 * 255) | int }}, 255, 0]}
{%- else -%}
  {"color": [255, {{ (255 - ([w, 3000] | min - 500) / 2500 * 255) | int }}, 0]}
{%- endif -%}
```
</details>

Toutes les clés de l'API de l'horloge fonctionnent : celles des [apps personnalisées AWTRIX 3](https://blueforcer.github.io/awtrix3/#/api?id=custom-apps-and-notifications) (`text`, `color`, `icon`, `draw`, `progress`, `blinkText`, `effect`…) ou des [apps poussées AWTRIX NG](https://github.com/Blueforcer/awtrix-ng/blob/main/docs/reference/payload.md) (`text`, `textColor`, `icon`, `draw`, `progress`, `textBlinkMs`, `effect`…). Écrivez `"icon": "2422"` pour utiliser une icône de la galerie LaMetric sans la télécharger sur l'horloge (un fichier de l'horloge nommé `2422` reste prioritaire ; `"lametric:2422"` prend toujours celle de la galerie).

## ❓ Questions fréquentes

<details>
<summary><b>Pourquoi ne pas simplement utiliser une automatisation ?</b></summary>

C'est possible, et c'est ce que font la plupart des gens aujourd'hui. Mais une automatisation qui rafraîchit l'horloge toutes les quelques secondes tourne des dizaines de milliers de fois par jour, et chaque exécution est écrite dans la base d'historique : l'état de l'automatisation, un événement, et une copie de chaque message envoyé. Avec le temps, la base grossit et les graphiques d'historique et d'énergie mettent du temps à s'ouvrir. Awtrix Studio n'envoie un message que quand l'affichage change vraiment, et n'écrit rien dans la base.
</details>

<details>
<summary><b>L'aperçu est-il exactement ce qu'affiche l'horloge ?</b></summary>

L'aperçu simulé est proche mais simplifié (police, défilement ; les effets de fond et la météo sont seulement nommés). Pour le rendu exact, passez sur l'onglet **Écran réel**, ou utilisez **Envoyer à l'horloge pour tester**.
</details>

<details>
<summary><b>Le miroir de l'écran ralentit-il quelque chose ?</b></summary>

Non. L'écran n'est demandé à l'horloge qu'une fois par seconde, et seulement quand le panneau est ouvert. Rien n'est écrit dans la base.
</details>

<details>
<summary><b>Puis-je garder mes automatisations ou blueprints actuels ?</b></summary>

Oui. Awtrix Studio ne touche qu'aux apps et indicateurs créés avec elle : tout le reste continue de fonctionner.
</details>

<details>
<summary><b>Comment passer une horloge d'AWTRIX 3 à AWTRIX NG ?</b></summary>

1. **Exportez** ses apps depuis le panneau, avant de flasher NG.
2. Flashez AWTRIX NG et activez MQTT dans la page web de l'horloge (**System → MQTT**), avec un préfixe.
3. **Ajoutez à nouveau l'horloge** dans Awtrix Studio, avec **Détecter automatiquement**. Pour garder le même préfixe, supprimez d'abord l'ancienne horloge.
4. **Importez** le fichier. Les apps simples passent telles quelles. Les apps en mode modèle sont signalées : réécrivez-les avec les clés de NG (voir les [exemples de modèles](#-exemples-de-modèles)) ; si l'horloge en refuse une, le panneau indique la clé en cause.
5. Les icônes désignées par un numéro étaient des fichiers de l'horloge, effacés par le flash de NG : Awtrix Studio envoie désormais l'icône LaMetric de ce numéro avec l'app.
6. Une fois que tout fonctionne, supprimez l'ancienne horloge si elle est encore là.
</details>

<details>
<summary><b>Que se passe-t-il quand l'horloge ou Home Assistant redémarre ?</b></summary>

Les apps sont renvoyées automatiquement, de même quand MQTT se reconnecte.
</details>

## 🔒 Sécurité

- Seuls les administrateurs voient le panneau et peuvent utiliser ses fonctions.
- Ce que vous tapez en mode simple est toujours traité comme du texte, jamais comme du code. Entités, couleurs et nombres sont vérifiés avant usage.
- Les noms d'app, qui finissent dans les topics MQTT, sont limités aux lettres, chiffres, `-` et `_`.
- Le panneau n'insère jamais de HTML brut : tout est affiché comme du texte.
- Le mode modèle exécute des modèles Home Assistant classiques, avec les mêmes garde-fous que partout ailleurs dans Home Assistant.

<details>
<summary><h2>🛠️ Sous le capot</h2></summary>

- Le modèle de chaque app est suivi par le moteur de modèles de Home Assistant : il n'est recalculé que quand une entité qu'il utilise change.
- Les changements de données sont limités à un message par seconde et par app au maximum.
- L'horloge annonce l'app à l'écran (`<prefix>/stats/currentApp` sous AWTRIX 3, `<prefix>/state/apps/active` sous AWTRIX NG) ; par défaut, les pages n'alternent que pendant que l'app est affichée.
- Un redémarrage se repère à l'uptime de l'horloge (`<prefix>/stats` sous AWTRIX 3), ou à son retour `online` (`<prefix>/availability` sous AWTRIX NG) ; les apps sont alors renvoyées.
- Sous AWTRIX NG, l'horloge répond à chaque app reçue (`…/result`) : un refus est affiché dans le panneau.
- Les messages partent directement par le client MQTT : pas d'appel de service, pas d'événement.
- Chaque app et chaque indicateur a une entité **interrupteur** : l'éteindre retire l'app de l'horloge (ou éteint l'indicateur) en gardant ses réglages. Son état survit aux redémarrages.
- L'action `awtrix_studio.refresh` renvoie toutes les apps et tous les indicateurs activés.
- Il n'y a volontairement pas de capteur « page actuelle » ou « dernière mise à jour » : ils écriraient sans cesse dans la base, précisément ce que l'intégration évite.

| Option des apps en mode modèle | Par défaut | À quoi ça sert |
|---|---|---|
| Durée par page | 1,5 s | Temps d'affichage de chaque page. |
| Alterner les pages seulement à l'écran | Activé | Seule la première page reste à jour quand une autre app est affichée. |
| Masquer si vide | Activé | Retire l'app de la rotation tant qu'il n'y a rien à afficher. |
| Durée de vie | 0 (désactivée) | L'horloge abandonne l'app si Home Assistant cesse de la rafraîchir. |

</details>

## 🤝 Contribuer

Rapports de bugs, idées et pull requests sont les bienvenus dans les [issues](https://github.com/3615nulsi/ha-awtrix-studio/issues). Publié sous [licence MIT](LICENSE).

---

<div align="center">

Fabriqué à Montpellier avec amour <3

</div>
