import difflib
import sys
import xml.dom.minidom


def pretty_print_xml(file_path):
    """
    Reads an XML file and returns a pretty-printed string.
    This ensures consistent formatting for better diff results.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            xml_str = f.read()
        dom = xml.dom.minidom.parseString(xml_str)
        pretty_xml = dom.toprettyxml(indent="  ")
        cleaned_lines = [line for line in pretty_xml.splitlines() if line.strip()]
        return cleaned_lines
    except FileNotFoundError:
        print(f"❌ File not found: {file_path}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        sys.exit(1)


def generate_side_by_side_diff(file1, file2, output_html):
    """
    Generates a side-by-side HTML diff between two XML files.
    """
    xml1_lines = pretty_print_xml(file1)
    xml2_lines = pretty_print_xml(file2)

    # Create HTML diff object
    diff = difflib.HtmlDiff(wrapcolumn=80)
    html_content = diff.make_file(xml1_lines, xml2_lines, fromdesc=file1, todesc=file2, context=False)

    # Save to file
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✅ Side-by-side diff saved to: {output_html}")
    print("📂 Open this file in your browser to view the comparison.")


if __name__ == "__main__":
    # Example usage: python script.py file1.xml file2.xml diff.html
    if len(sys.argv) != 4:
        print("Usage: python script.py <file1.xml> <file2.xml> <output.html>")
        sys.exit(1)

    file1, file2, output_html = sys.argv[1], sys.argv[2], sys.argv[3]
    generate_side_by_side_diff(file1, file2, output_html)
