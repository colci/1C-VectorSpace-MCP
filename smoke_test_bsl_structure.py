from bsl_structure import (
    analyze_bsl_structure,
    return_has_value,
    strip_bsl_inline_comment,
)


def messages(result: dict) -> list[str]:
    return [issue["message"] for issue in result["issues"]]


def main() -> int:
    assert strip_bsl_inline_comment('Сообщить("http://host//path"); // comment').strip() == 'Сообщить("http://host//path");'
    assert return_has_value("Возврат Результат;")
    assert return_has_value("Return Result;")
    assert not return_has_value("Возврат;")
    assert not return_has_value("Return; // no value")

    valid = analyze_bsl_structure(
        """
&НаСервере
Функция ПолучитьЗначение()
    Возврат 1;
КонецФункции

Процедура Выполнить()
    Возврат;
КонецПроцедуры
"""
    )
    assert valid["method_count"] == 2
    assert valid["error_count"] == 0
    assert valid["warning_count"] == 0

    no_return = analyze_bsl_structure(
        """
Функция ПолучитьЗначение()
    Результат = 1;
КонецФункции
"""
    )
    assert no_return["error_count"] == 0
    assert no_return["warning_count"] == 1
    assert any("не содержит явного `Возврат`" in message for message in messages(no_return))

    procedure_value_return = analyze_bsl_structure(
        """
Процедура Выполнить()
    Возврат 1;
КонецПроцедуры
"""
    )
    assert procedure_value_return["error_count"] == 1
    assert any("содержит `Возврат` со значением" in message for message in messages(procedure_value_return))

    structural_errors = analyze_bsl_structure(
        """
&НеизвестнаяДиректива
#Область Test
#Если Клиент Тогда
Процедура НеЗакрыта()
"""
    )
    assert structural_errors["error_count"] == 3
    assert structural_errors["warning_count"] == 1
    assert any("Неизвестная или неподдержанная директива" in message for message in messages(structural_errors))
    assert any("не закрыт до конца файла" in message for message in messages(structural_errors))
    assert any("Область не закрыта" in message for message in messages(structural_errors))
    assert any("Препроцессорное условие не закрыто" in message for message in messages(structural_errors))

    duplicate = analyze_bsl_structure(
        """
Процедура Выполнить()
КонецПроцедуры

Procedure Выполнить()
EndProcedure
"""
    )
    assert duplicate["error_count"] == 1
    assert any("Повторное объявление метода" in message for message in messages(duplicate))

    print("[bsl-structure-smoke] success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
