"""A subject whose title starts with an assessment-like word is still a subject.

Mirrors the ENSAI "BULLETIN DÉTAILLÉ" layout: subjects are flush rows, assessments carry
a leading dash. "PROJET INFORMATIQUE" (coef 4) used to be swallowed as an assessment of the
previous subject because its title starts with "projet".
"""

from __future__ import annotations

from decimal import Decimal

from backend.app.modules.ingestion.connectors.pamplemousse import parse_pamplemousse_notes_html

BULLETIN = """
<html><body><h2>BULLETIN DÉTAILLÉ</h2>
<table>
  <tr><th>Enseignement</th><th>Note finale</th><th>Coefficient</th><th>Classement</th></tr>
  <tr><td>Moyenne générale</td><td>14,50</td><td>8,50</td><td></td></tr>
  <tr><td>PREMIER SEMESTRE</td><td>14,50</td><td></td><td></td></tr>
  <tr><td>UE2-03 : INFORMATIQUE POUR LA DATA SCIENCE</td><td>14,04</td><td></td><td></td></tr>
  <tr><td>COMPLÉMENTS D'INFORMATIQUE</td><td>12,13</td><td>2,00</td><td></td></tr>
  <tr><td>- EXAMEN</td><td>12,13</td><td>0,75</td><td></td></tr>
  <tr><td>PROJET INFORMATIQUE</td><td>15,00</td><td>4,00</td><td></td></tr>
  <tr><td>- RAPPORT INTERMÉDIAIRE</td><td>15,00</td><td>1,00</td><td></td></tr>
</table></body></html>
"""


def test_projet_informatique_is_a_subject_with_its_own_assessment():
    records = parse_pamplemousse_notes_html(BULLETIN, academic_year="2026-2027")
    by_title = {r.title: r for r in records if r.record_type in ("subject", "assessment")}

    projet = by_title["PROJET INFORMATIQUE"]
    complements = by_title["COMPLÉMENTS D'INFORMATIQUE"]
    assert projet.record_type == "subject" and projet.weight == Decimal("4.00")
    assert complements.record_type == "subject"

    assert by_title["- EXAMEN"].parent_external_id == complements.external_id
    assert by_title["- RAPPORT INTERMÉDIAIRE"].parent_external_id == projet.external_id

    finals = {r.parent_external_id: r.grade_value for r in records
              if r.record_type == "aggregate_grade" and r.parent_external_id in (projet.external_id, complements.external_id)}
    assert finals == {projet.external_id: Decimal("15.00"), complements.external_id: Decimal("12.13")}
