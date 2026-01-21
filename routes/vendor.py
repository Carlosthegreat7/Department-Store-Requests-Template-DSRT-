from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import Vendor
from portal import loggedin_required

# Create the Blueprint for Vendor logic
vendor_bp = Blueprint('vendor', __name__)

@vendor_bp.route('/admin/add_vendor', methods=['GET', 'POST'])
@loggedin_required()
def add_vendor():
    """Route to add a new vendor to the MySQL database."""
    if request.method == 'POST':
        # Capture and clean form data
        code = request.form.get('code', '').strip().zfill(6)
        name = request.form.get('name', '').strip().upper()
        
        try:
            # Create new Vendor instance
            new_vendor = Vendor(vendor_code=code, vendor_name=name)
            db.session.add(new_vendor)
            db.session.commit()
            flash(f"Successfully added Vendor: {name}", "success")
            # Redirect back to management with the vendor tab anchor
            return redirect(url_for('admin_management', _anchor='vendor'))
        except Exception as e:
            db.session.rollback()
            flash(f"Database Error: {e}", "danger")
            
    return render_template('add_vendor.html')

@vendor_bp.route('/admin/edit_vendor/<code>', methods=['GET', 'POST'])
@loggedin_required()
def edit_vendor(code):
    """Route to update an existing vendor's details."""
    # Find the vendor by primary key or return 404
    vendor = Vendor.query.get_or_404(code)
    
    if request.method == 'POST':
        try:
            # Update values from form
            vendor.vendor_code = request.form.get('code').strip().zfill(6)
            vendor.vendor_name = request.form.get('name').strip().upper()
            
            db.session.commit()
            flash(f"Vendor {vendor.vendor_name} updated successfully!", "success")
            return redirect(url_for('admin_management', _anchor='vendor'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating vendor: {e}", "danger")
            
    return render_template('edit_vendor.html', vendor=vendor, old_code=code)

@vendor_bp.route('/admin/delete_vendor/<code>', methods=['POST'])
@loggedin_required()
def delete_vendor(code):
    """Route to remove a vendor from the database."""
    vendor = Vendor.query.get(code)
    if vendor:
        try:
            db.session.delete(vendor)
            db.session.commit()
            flash(f"Vendor {code} deleted successfully.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error deleting vendor: {e}", "danger")
    else:
        flash("Vendor not found.", "warning")
        
    return redirect(url_for('admin_management', _anchor='vendor'))