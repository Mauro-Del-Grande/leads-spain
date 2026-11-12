import pytest

from app.plantillas import marca_final, marca_heuristica, marca_valida


@pytest.mark.parametrize("titulo,ciudad,esperada", [
    ("Panadería y confitería. Dulce Sabor 2", "", "Dulce Sabor"),
    ("Panadería La Espiga", "", "La Espiga"),
    ("Kali Indumentaria", "", "Kali"),
    ("Verde Vivero", "", "Verde Vivero"),
    ("MUSUIKA | TIENDA DE ROPA MDP", "", "MUSUIKA"),
    ("Marbellas Ropa en Mar del Plata", "Mar del Plata", "Marbellas"),
    ("Olimpo Centro de Entrenamiento", "", "Olimpo"),
    ("Panadería & Confitería Tres Arroyos", "Tres Arroyos", "Panadería & Confitería Tres Arroyos"),
    ("La Rosa", "", "La Rosa"),
    # Espana: forma juridica y ciudad
    ("Panadería La Espiga S.L.", "", "La Espiga"),
    ("Horno Santa Clara, S.L.U.", "", "Santa Clara"),
    ("La Tasca de Pepe", "", "La Tasca de Pepe"),
    ("Kali Moda Madrid", "Madrid", "Kali"),
])
def test_marca_heuristica(titulo, ciudad, esperada):
    assert marca_heuristica(titulo, ciudad) == esperada


def test_marca_valida():
    assert marca_valida("Dulce Sabor", "Panadería y confitería. Dulce Sabor 2")
    assert marca_valida("La Favorita", "Panadería-confitería-sandwichería La Favorita")
    assert not marca_valida("Iron Temple Fitness", "Sport Gym")
    assert not marca_valida("", "X")


def test_marca_final_prioridades():
    lead = {"negocio": "Panadería La Espiga", "marca_ia": "La Espiga"}
    assert marca_final(lead) == "La Espiga"
    assert marca_final({**lead, "marca_manual": "Espiga TA"}) == "Espiga TA"
    assert marca_final({**lead, "marca_ia": "Inventada"}) == "La Espiga"
