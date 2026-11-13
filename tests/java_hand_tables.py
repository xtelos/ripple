"""The hand-written JDK tables that java_resolver.py used before it read ripple/data/java_jdk.txt, frozen as they were.

They were read off JDK 21 with `javap` by hand and reviewed twice, so they are a source independent of the generated
table. The resolver no longer imports them: tests/test_java_jdk.py holds the generated table against them, and
tests/test_java_jdk_use.py uses them as the baseline a naive wiring would reproduce. Do not edit a value here to make a test
pass: where the table disagrees with a value, the disagreement is named in the test with the side the class files support.
"""

from __future__ import annotations

OBJECT = "java.lang.Object"

# The members of the three supertypes the language itself puts under every class, enum and record (JLS 8.1.4, 8.9.3,
# 8.10.3), read off java.lang.Object, java.lang.Enum and java.lang.Record with `javap -protected` on JDK 21. Nothing
# else about the JDK is known, and no other external type is trusted to lack a name.
#
# Object's methods with the parameter types of each arity. A repo method of the same name and arity either repeats
# these (it overrides Object's) or does not (an overload that a call of Object's may be meant for).
OBJECT_SIGNATURES: dict[tuple[str, int], tuple[str, ...]] = {
    ("equals", 1): (OBJECT,), ("wait", 1): ("long",), ("wait", 2): ("long", "int"),
    **{(name, 0): () for name in ("getClass", "hashCode", "clone", "toString", "notify", "notifyAll", "wait", "finalize")},
}  # fmt: skip
OBJECT_MEMBERS = frozenset(name for name, _ in OBJECT_SIGNATURES)
# What java.lang.Enum adds to Object's (any arity: `valueOf` is declared with one and two parameters, and a repo
# enum may overload `valueOf` or `compareTo`). java.lang.Record adds nothing: its three methods are Object's.
ENUM_MEMBERS = frozenset({"name", "ordinal", "compareTo", "getDeclaringClass", "describeConstable", "values", "valueOf"})
# The supertypes outside the repo whose members are known: member name -> the numbers of parameters it is declared with
# (ANY: not known, which is any number). A name, or a number of arguments, that is not in the entry is not that type's, so
# a call of it is not one that such a supertype could answer. Enum and Record are put under every enum and record by the
# language; every entry of KNOWN_SUPERTYPES below is known the same way (what the type and everything above it
# declares). A marker interface (Serializable, Cloneable, RandomAccess, EventListener) declares nothing at all.
ANY = (-1,)
KNOWN_MEMBERS: dict[str, dict[str, tuple[int, ...]]] = {"java.lang.Record": {}}

# The well known JDK types whose whole supertype list is known, with that list (java.lang.Object left out), and which
# declare no member type that a subclass inherits (a public or protected nested type: `Map.Entry`, `Thread.State`,
# `Enum.EnumDesc`). Read off JDK 21 with `javap -v -p` (the InnerClasses entries of each, and the supers of its
# header, closed over the JDK's own types). A repo type with any other external supertype may inherit a member type of
# that supertype, which hides a type of the same name from an outer scope or an import, and may itself be an instance
# of any type that supertype extends: neither is known, so a name or a call that depends on it is not proven.
KNOWN_SUPERTYPES: dict[str, tuple[str, ...]] = {
    "java.io.Serializable": (), "java.lang.Cloneable": (), "java.lang.Comparable": (), "java.lang.Runnable": (),
    "java.util.concurrent.Callable": (), "java.lang.AutoCloseable": (), "java.lang.Iterable": (), "java.util.Iterator": (),
    "java.lang.CharSequence": (), "java.lang.Appendable": (), "java.util.RandomAccess": (), "java.util.Comparator": (),
    "java.util.function.Supplier": (), "java.util.function.Function": (), "java.util.function.Consumer": (),
    "java.util.function.Predicate": (), "java.util.function.BiFunction": (), "java.util.function.BiConsumer": (),
    "java.io.Flushable": (), "java.util.EventListener": (), "java.lang.annotation.Annotation": (), "java.lang.Record": (),
    "java.util.Map.Entry": (), "java.lang.reflect.Type": (), "java.lang.reflect.InvocationHandler": (),
    "java.util.concurrent.ThreadFactory": (),
    "java.io.Closeable": ("java.lang.AutoCloseable",),
    "java.lang.reflect.ParameterizedType": ("java.lang.reflect.Type",),
    "java.lang.reflect.GenericArrayType": ("java.lang.reflect.Type",),
    "java.lang.reflect.WildcardType": ("java.lang.reflect.Type",),
    "java.lang.reflect.TypeVariable": ("java.lang.reflect.AnnotatedElement", "java.lang.reflect.Type"),
    "java.util.ListIterator": ("java.util.Iterator",),
    "java.util.Random": ("java.io.Serializable", "java.util.random.RandomGenerator"),
    "java.beans.VetoableChangeListener": ("java.util.EventListener",),
    "java.util.function.UnaryOperator": ("java.util.function.Function",),
    "java.util.function.BinaryOperator": ("java.util.function.BiFunction",),
    "java.lang.Number": ("java.io.Serializable",),
    "java.lang.Throwable": ("java.io.Serializable",),
    "java.lang.Enum": ("java.io.Serializable", "java.lang.Comparable", "java.lang.constant.Constable"),
    "java.lang.Exception": ("java.io.Serializable", "java.lang.Throwable"),
    "java.lang.Error": ("java.io.Serializable", "java.lang.Throwable"),
    "java.io.IOException": ("java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    "java.lang.InterruptedException": ("java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    "java.lang.CloneNotSupportedException": ("java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    "java.lang.ReflectiveOperationException": ("java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    "java.lang.AssertionError": ("java.io.Serializable", "java.lang.Error", "java.lang.Throwable"),
    "java.io.FileNotFoundException": ("java.io.IOException", "java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    "java.lang.RuntimeException": ("java.io.Serializable", "java.lang.Exception", "java.lang.Throwable"),
    **{
        name: ("java.io.Serializable", "java.lang.Exception", "java.lang.RuntimeException", "java.lang.Throwable")
        for name in (
            "java.lang.IllegalArgumentException", "java.lang.IllegalStateException", "java.lang.UnsupportedOperationException",
            "java.lang.ClassCastException", "java.lang.NullPointerException", "java.lang.IndexOutOfBoundsException",
            "java.lang.SecurityException", "java.lang.ArithmeticException", "java.util.NoSuchElementException",
            "java.util.ConcurrentModificationException", "java.io.UncheckedIOException",
        )
    },
    "java.lang.NumberFormatException": (
        "java.io.Serializable", "java.lang.Exception", "java.lang.IllegalArgumentException", "java.lang.RuntimeException",
        "java.lang.Throwable",
    ),
}  # fmt: skip
# the exceptions to "declares none": java.util.Random inherits the member interfaces of RandomGenerator (JDK 17 and up)
MEMBER_TYPES: dict[str, frozenset[str]] = {
    "java.lang.Enum": frozenset({"EnumDesc"}),
    "java.util.Random": frozenset({"StreamableGenerator", "SplittableGenerator", "JumpableGenerator", "LeapableGenerator", "ArbitrarilyJumpableGenerator"}),
}  # fmt: skip

# The public and protected methods and fields (static ones too) that each type of KNOWN_SUPERTYPES, and each type it
# names above it, declares itself: `javap -protected` on JDK 21, nothing from a later JDK is in it. KNOWN_MEMBERS is the
# union over a type and its supertypes.
DECLARED_MEMBERS: dict[str, dict[str, tuple[int, ...]]] = {
    "java.beans.VetoableChangeListener": {"vetoableChange": (1,)},
    "java.io.Closeable": {"close": (0,)},
    "java.io.FileNotFoundException": {},
    "java.io.Flushable": {"flush": (0,)},
    "java.io.IOException": {},
    "java.io.Serializable": {},
    "java.io.UncheckedIOException": {"getCause": (0,)},
    "java.lang.Appendable": {"append": (1, 3)},
    "java.lang.ArithmeticException": {},
    "java.lang.AssertionError": {},
    "java.lang.AutoCloseable": {"close": (0,)},
    "java.lang.CharSequence": {
        "charAt": (1,), "chars": (0,), "codePoints": (0,), "isEmpty": (0,), "length": (0,), "subSequence": (2,),
        "toString": (0,)
    },
    "java.lang.ClassCastException": {},
    "java.lang.CloneNotSupportedException": {},
    "java.lang.Cloneable": {},
    "java.lang.Comparable": {"compareTo": (1,)},
    "java.lang.Enum": {
        "clone": (0,), "compareTo": (1,), "describeConstable": (0,), "equals": (1,), "finalize": (0,),
        "getDeclaringClass": (0,), "hashCode": (0,), "name": (0,), "ordinal": (0,), "toString": (0,), "valueOf": (2,)
    },
    "java.lang.Error": {},
    "java.lang.Exception": {},
    "java.lang.IllegalArgumentException": {},
    "java.lang.IllegalStateException": {},
    "java.lang.IndexOutOfBoundsException": {},
    "java.lang.InterruptedException": {},
    "java.lang.Iterable": {"forEach": (1,), "iterator": (0,), "spliterator": (0,)},
    "java.lang.NullPointerException": {"fillInStackTrace": (0,), "getMessage": (0,)},
    "java.lang.Number": {
        "byteValue": (0,), "doubleValue": (0,), "floatValue": (0,), "intValue": (0,), "longValue": (0,), "shortValue":
        (0,)
    },
    "java.lang.NumberFormatException": {},
    "java.lang.ReflectiveOperationException": {},
    "java.lang.Runnable": {"run": (0,)},
    "java.lang.RuntimeException": {},
    "java.lang.SecurityException": {},
    "java.lang.Throwable": {
        "addSuppressed": (1,), "fillInStackTrace": (0,), "getCause": (0,), "getLocalizedMessage": (0,), "getMessage":
        (0,), "getStackTrace": (0,), "getSuppressed": (0,), "initCause": (1,), "printStackTrace": (0, 1),
        "setStackTrace": (1,), "toString": (0,)
    },
    "java.lang.UnsupportedOperationException": {},
    "java.lang.annotation.Annotation": {"annotationType": (0,), "equals": (1,), "hashCode": (0,), "toString": (0,)},
    "java.lang.constant.Constable": {"describeConstable": (0,)},
    "java.lang.reflect.AnnotatedElement": {
        "getAnnotation": (1,), "getAnnotations": (0,), "getAnnotationsByType": (1,), "getDeclaredAnnotation": (1,),
        "getDeclaredAnnotations": (0,), "getDeclaredAnnotationsByType": (1,), "isAnnotationPresent": (1,)
    },
    "java.lang.reflect.GenericArrayType": {"getGenericComponentType": (0,)},
    "java.lang.reflect.InvocationHandler": {"invoke": (3,)},
    "java.lang.reflect.ParameterizedType": {"getActualTypeArguments": (0,), "getOwnerType": (0,), "getRawType": (0,)},
    "java.lang.reflect.Type": {"getTypeName": (0,)},
    "java.lang.reflect.TypeVariable": {
        "getAnnotatedBounds": (0,), "getBounds": (0,), "getGenericDeclaration": (0,), "getName": (0,)
    },
    "java.lang.reflect.WildcardType": {"getLowerBounds": (0,), "getUpperBounds": (0,)},
    "java.util.Comparator": {
        "compare": (2,), "equals": (1,), "reversed": (0,), "thenComparing": (1, 2), "thenComparingDouble": (1,),
        "thenComparingInt": (1,), "thenComparingLong": (1,)
    },
    "java.util.ConcurrentModificationException": {},
    "java.util.EventListener": {},
    "java.util.Iterator": {"forEachRemaining": (1,), "hasNext": (0,), "next": (0,), "remove": (0,)},
    "java.util.ListIterator": {
        "add": (1,), "hasNext": (0,), "hasPrevious": (0,), "next": (0,), "nextIndex": (0,), "previous": (0,),
        "previousIndex": (0,), "remove": (0,), "set": (1,)
    },
    "java.util.Map.Entry": {"equals": (1,), "getKey": (0,), "getValue": (0,), "hashCode": (0,), "setValue": (1,)},
    "java.util.NoSuchElementException": {},
    "java.util.Random": {
        "doubles": (0, 1, 2, 3), "from": (1,), "ints": (0, 1, 2, 3), "longs": (0, 1, 2, 3), "next": (1,), "nextBoolean":
        (0,), "nextBytes": (1,), "nextDouble": (0,), "nextFloat": (0,), "nextGaussian": (0,), "nextInt": (0, 1),
        "nextLong": (0,), "setSeed": (1,)
    },
    "java.util.RandomAccess": {},
    "java.util.concurrent.Callable": {"call": (0,)},
    "java.util.concurrent.ThreadFactory": {"newThread": (1,)},
    "java.util.function.BiConsumer": {"accept": (2,), "andThen": (1,)},
    "java.util.function.BiFunction": {"andThen": (1,), "apply": (2,)},
    "java.util.function.BinaryOperator": {},
    "java.util.function.Consumer": {"accept": (1,), "andThen": (1,)},
    "java.util.function.Function": {"andThen": (1,), "apply": (1,), "compose": (1,)},
    "java.util.function.Predicate": {"and": (1,), "negate": (0,), "or": (1,), "test": (1,)},
    "java.util.function.Supplier": {"get": (0,)},
    "java.util.function.UnaryOperator": {},
    "java.util.random.RandomGenerator": {
        "doubles": (0, 1, 2, 3), "ints": (0, 1, 2, 3), "isDeprecated": (0,), "longs": (0, 1, 2, 3), "nextBoolean": (0,),
        "nextBytes": (1,), "nextDouble": (0, 1, 2), "nextExponential": (0,), "nextFloat": (0, 1, 2), "nextGaussian":
        (0, 2), "nextInt": (0, 1, 2), "nextLong": (0, 1, 2)
    },
}  # fmt: skip


def _known_members(name: str) -> dict[str, tuple[int, ...]]:
    """What a type of KNOWN_SUPERTYPES and everything above it declare: member -> the numbers of parameters."""
    found: dict[str, set[int]] = {}
    for above in (name, *KNOWN_SUPERTYPES[name]):
        for member, counts in DECLARED_MEMBERS.get(above, {}).items():
            found.setdefault(member, set()).update(counts)
    return {member: tuple(sorted(counts)) for member, counts in found.items()}


KNOWN_MEMBERS.update({name: _known_members(name) for name in KNOWN_SUPERTYPES if name != "java.lang.Record"})
KNOWN_MEMBERS["java.lang.Enum"].update({member: ANY for member in ENUM_MEMBERS})
# JDK classes that are final, so no repo type is an instance of one (`javap` prints `final class`): a call on a value
# of such a type cannot run repo code.
FINAL_EXTERNAL = frozenset(
    {
        "java.lang." + n
        for n in ("String", "StringBuilder", "StringBuffer", "Integer", "Long", "Short", "Byte", "Character", "Boolean", "Double",
                  "Float", "Class", "Math", "StrictMath", "System", "Void", "StackTraceElement", "ProcessBuilder")
    }
    | {
        "java.util." + n
        for n in ("Objects", "Optional", "OptionalInt", "OptionalLong", "OptionalDouble", "Arrays", "UUID", "Scanner", "StringJoiner", "Locale")
    }
    | {"java.util.regex.Pattern", "java.util.regex.Matcher"}
)  # fmt: skip
