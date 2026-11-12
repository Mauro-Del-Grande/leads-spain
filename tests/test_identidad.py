import pytest

from app.identidad import (claves_de, es_movil_probable, estado_mayor, normalizar_dominio,
                           normalizar_email, normalizar_nombre, normalizar_telefono,
                           normalizar_usuario_ig, nucleo_nombre, whatsapp_numero)

AREA = ""
ESPERADO = "+34612345678"


@pytest.mark.parametrize("crudo", [
    "612 34 56 78", "612345678", "+34 612 34 56 78", "+34612345678", "0034 612 345 678",
    "(+34) 612-34-56-78", "34 612 34 56 78", "612.34.56.78",
])
def test_telefonos_misma_forma(crudo):
    assert normalizar_telefono(crudo, AREA) == ESPERADO


@pytest.mark.parametrize("crudo,esperado", [
    ("91 123 45 67", "+34911234567"),
    ("+34 93 123 45 67", "+34931234567"),
    ("954 11 22 33", "+34954112233"),
    ("722 11 22 33", "+34722112233"),
])
def test_telefonos_fijos_y_moviles(crudo, esperado):
    assert normalizar_telefono(crudo) == esperado


@pytest.mark.parametrize("basura", ["consultar", "1234", "", None, "+1 212 555 1234", "900 123 456",
                                    "902 10 20 30", "+54 9 2983 456789", "512345678"])
def test_telefono_basura_no_genera_clave(basura):
    assert normalizar_telefono(basura, AREA) == ""


def test_whatsapp_solo_moviles():
    assert whatsapp_numero("+34612345678") == "34612345678"
    assert whatsapp_numero("+34911234567") == ""
    assert whatsapp_numero("") == ""


def test_movil_probable():
    assert es_movil_probable("612 34 56 78") is True
    assert es_movil_probable("+34 722 11 22 33") is True
    assert es_movil_probable("91 123 45 67") is False
    assert es_movil_probable("") is None


@pytest.mark.parametrize("crudo", ["@LaEspiga.TA", "laespiga.ta", "https://instagram.com/laespiga.ta",
                                   "instagram.com/laespiga.ta/", "  @laespiga.ta  ",
                                   "https://www.instagram.com/laespiga.ta/?hl=es"])
def test_usuario_ig(crudo):
    assert normalizar_usuario_ig(crudo) == "laespiga.ta"


@pytest.mark.parametrize("crudo", ["https://instagram.com/p/Cxyz123", "https://facebook.com/laespiga",
                                   "https://instagram.com/explore", "no valido!!"])
def test_usuario_ig_invalido(crudo):
    assert normalizar_usuario_ig(crudo) == ""


def test_dominios():
    assert normalizar_dominio("https://www.LaEspiga.com.ar/contacto?x=1") == "laespiga.com.ar"
    assert normalizar_dominio("https://confiterialaperla.com.ar/") == "confiterialaperla.com.ar"


@pytest.mark.parametrize("web", [
    "https://www.google.com/maps/search/?api=1&query=X", "https://maps.app.goo.gl/abc",
    "https://www.facebook.com/pages/La-Fe-Bakery/1136", "https://instagram.com/laespiga.ta",
    "linktr.ee/laespiga", "https://wa.me/5492983456789", "https://www.mitiendanube.com",
    "https://sites.google.com/view/laespiga", "https://z-p15.www.instagram.com/facheritas.accesorios/",
    "https://api.whatsapp.com/send?phone=549",
])
def test_dominios_genericos_no_son_web_propia(web):
    assert normalizar_dominio(web) == ""


def test_subdominio_de_plataforma_si_identifica():
    assert normalizar_dominio("https://kaimasu.mitiendanube.com/") == "kaimasu.mitiendanube.com"


def test_emails():
    assert normalizar_email("  Info@LaEspiga.com.ar ") == "info@laespiga.com.ar"
    assert normalizar_email("mailto:hola@x.com") == "hola@x.com"
    assert normalizar_email("user@example.com") == ""
    assert normalizar_email("logo@2x.png") == ""
    assert normalizar_email("no es mail") == ""


def test_nombres():
    assert normalizar_nombre("Panadería La Espiga, S.L.U.") == "panaderia la espiga"
    assert nucleo_nombre("Panadería La Espiga S.L.") == "espiga"
    assert nucleo_nombre("Kiosco A") == ""


def test_regresion_nucleo_igual_a_ciudad():
    claves = claves_de({"negocio": "Panadería Valladolid", "ciudad": "Valladolid"}, AREA)
    assert not any(c.startswith("nuc:valladolid") for c in claves)


def test_formato_de_claves():
    c = claves_de({"negocio": "Panadería La Espiga S.L.", "ciudad": "Madrid",
                   "telefono_raw": "91 123 45 67", "instagram": "@laespiga.mad",
                   "web": "https://laespiga.es"}, AREA)
    assert c == ["tel:+34911234567", "ig:laespiga.mad", "web:laespiga.es",
                 "nom:panaderia la espiga|madrid", "nuc:espiga|madrid"]


def test_estado_mayor():
    assert estado_mayor("nuevo", "contactado") == "contactado"
    assert estado_mayor("cliente", "contactado") == "cliente"
    assert estado_mayor("respondio", "no molestar") == "no molestar"
    assert estado_mayor("nuevo", "cualquier cosa") == "nuevo"
