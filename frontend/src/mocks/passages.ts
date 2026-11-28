// Synthetic passages for the demo library: short, generic textbook sentences written
// for the demo (no real course material). Keys are document titles from fixtures.ts.
import type { CitationLocator } from "@/api/types";

export interface MockPassage {
  locator: CitationLocator;
  text: string;
}

const page = (n: number): CitationLocator => ({ kind: "page", page: n });
const slide = (n: number): CitationLocator => ({ kind: "slide", slide: n });
const cell = (n: number): CitationLocator => ({ kind: "cell", cell: n });

export const PASSAGES: Record<string, MockPassage[]> = {
  "Chapitre 1 — Estimation ponctuelle (diapositives)": [
    { locator: slide(4), text: "Un estimateur est une statistique utilisée pour approcher un paramètre inconnu. Son biais est la différence entre son espérance et la vraie valeur du paramètre." },
    { locator: slide(7), text: "Le risque quadratique se décompose en la somme de la variance de l'estimateur et du carré de son biais." },
    { locator: slide(12), text: "L'estimateur du maximum de vraisemblance est la valeur du paramètre qui maximise la vraisemblance des observations. On maximise en pratique la log-vraisemblance." },
  ],
  "Chapitre 2 — Intervalles de confiance (diapositives)": [
    { locator: slide(3), text: "Un intervalle de confiance de niveau 95 % est un intervalle aléatoire qui contient le vrai paramètre avec une probabilité de 0,95 avant observation des données." },
    { locator: slide(9), text: "La méthode du pivot consiste à trouver une fonction des données et du paramètre dont la loi ne dépend pas du paramètre, puis à l'inverser." },
    { locator: slide(14), text: "Pour une moyenne avec variance inconnue et un échantillon gaussien, on utilise la loi de Student à n - 1 degrés de liberté." },
  ],
  "Chapitre 3 — Tests d'hypothèses (diapositives)": [
    { locator: slide(5), text: "L'erreur de première espèce consiste à rejeter l'hypothèse nulle alors qu'elle est vraie ; sa probabilité maximale est le niveau du test." },
    { locator: slide(8), text: "L'erreur de seconde espèce consiste à conserver l'hypothèse nulle alors qu'elle est fausse. La puissance du test vaut un moins cette probabilité." },
    { locator: slide(15), text: "La p-valeur est la probabilité, sous l'hypothèse nulle, d'observer une statistique au moins aussi extrême que celle obtenue. On rejette si elle est inférieure au niveau choisi." },
  ],
  "TD 3 — Intervalles de confiance": [
    { locator: page(1), text: "Exercice 1. On observe un échantillon gaussien de taille 25 de variance inconnue. Construire un intervalle de confiance de niveau 95 % pour la moyenne." },
    { locator: page(2), text: "Exercice 3. Proposer une quantité pivotale pour le paramètre d'une loi exponentielle et en déduire un intervalle de confiance." },
  ],
  "TD 4 — Tests": [
    { locator: page(1), text: "Exercice 2. Calculer la puissance du test de niveau 5 % pour une alternative unilatérale et commenter l'effet de la taille d'échantillon." },
    { locator: page(3), text: "Exercice 4. Interpréter une p-valeur de 0,03 pour un test de Student bilatéral au niveau 5 %." },
  ],
  "Partiel 2024": [
    { locator: page(1), text: "Question 1. Montrer que l'estimateur proposé est sans biais et calculer son risque quadratique." },
    { locator: page(2), text: "Question 3. Construire un test de niveau 5 % et donner sa règle de décision en fonction de la p-valeur." },
  ],
  "Partiel 2024 — corrigé": [
    { locator: page(1), text: "Corrigé de la question 1 : l'espérance de l'estimateur est égale au paramètre, il est donc sans biais ; son risque quadratique se réduit à sa variance." },
  ],
  "Fiche de synthèse — lois usuelles": [
    { locator: page(1), text: "Loi de Student : utilisée pour une moyenne lorsque la variance est estimée. Loi du khi-deux : utilisée pour une variance d'échantillon gaussien." },
  ],
  "Polycopié — Convergences": [
    { locator: page(6), text: "La loi des grands nombres affirme que la moyenne empirique converge vers l'espérance lorsque la taille de l'échantillon tend vers l'infini." },
    { locator: page(11), text: "Le théorème central limite affirme que la moyenne empirique centrée et normalisée converge en loi vers une loi normale centrée réduite." },
  ],
  "Polycopié — Vecteurs gaussiens": [
    { locator: page(4), text: "Pour un vecteur gaussien, deux composantes décorrélées sont indépendantes ; ce résultat est faux pour des variables quelconques." },
  ],
  "TD 2 — Théorème central limite": [
    { locator: page(1), text: "Exercice 1. Utiliser le théorème central limite pour approcher la probabilité qu'une somme de 100 variables de Bernoulli dépasse 60." },
  ],
  "Cours 1 — Complexité": [
    { locator: slide(6), text: "La notation grand O décrit une borne supérieure de la croissance du nombre d'opérations en fonction de la taille de l'entrée." },
    { locator: slide(18), text: "Le tri fusion a une complexité en O(n log n) dans tous les cas, tandis que le tri rapide est en O(n²) dans le pire cas." },
  ],
  "Cours 5 — Graphes": [
    { locator: slide(9), text: "Le parcours en largeur visite les sommets par distance croissante à la source à l'aide d'une file." },
    { locator: slide(21), text: "L'algorithme de Dijkstra calcule les plus courts chemins depuis une source lorsque tous les poids des arêtes sont positifs." },
  ],
  "Notebook — parcours de graphes": [{ locator: cell(3), text: "Implémentation du parcours en largeur avec une file : on marque chaque sommet visité pour ne pas le traiter deux fois." }],
};
