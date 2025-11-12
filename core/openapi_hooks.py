# core/openapi_hooks.py

def fix_nullable_without_type(result, generator, request, public):
    """
    Redocly complains when a property has `nullable: true` without an explicit `type`.
    This post-processing hook walks through components and adds a default type ('string')
    when `nullable` is set but `type` is missing.
    """
    try:
        components = result.get("components", {}).get("schemas", {})
        for _schema_name, schema in components.items():
            props = schema.get("properties", {})
            for _prop_name, prop in props.items():
                if isinstance(prop, dict) and prop.get("nullable") and "type" not in prop:
                    # If the field is a choice/oneOf, string is a safe fallback type.
                    prop.setdefault("type", "string")
    except Exception:
        # Never let the hook crash schema generation
        pass
    return result