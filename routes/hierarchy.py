from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import Brand
from portal import loggedin_required

# Create the Blueprint for Hierarchy (Brand) logic
hierarchy_bp = Blueprint('hierarchy', __name__)

@hierarchy_bp.route('/admin/add_hierarchy', methods=['GET', 'POST'])
@loggedin_required()
def add_hierarchy():
    """Route to add a new Brand/Hierarchy entry to the MySQL database."""
    if request.method == 'POST':
        try:
            # Capture data using names matching the redesigned add_hierarchy.html
            new_brand = Brand(
                brand_name=request.form.get('brand_name', '').strip().upper(),
                product_group=request.form.get('product_group', '').strip().upper(),
                dept_code=request.form.get('dept_code', '').strip().zfill(3),
                sub_dept_code=request.form.get('sub_dept_code', '').strip().zfill(3),
                class_code=request.form.get('class_code', '').strip().zfill(3)
            )
            
            db.session.add(new_brand)
            db.session.commit()
            
            flash(f"Successfully added Brand: {new_brand.brand_name}", "success")
            # FIXED: Added _anchor to persist the Hierarchy tab state
            return redirect(url_for('admin_management', _anchor='hierarchy'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Database Error: {e}", "danger")
            
    return render_template('add_hierarchy.html')

@hierarchy_bp.route('/admin/edit_hierarchy/<code>', methods=['GET', 'POST'])
@loggedin_required()
def edit_hierarchy(code):
    """Route to update an existing Brand entry."""
    # Find the hierarchy entry by the old brand name (code)
    brand = Brand.query.filter_by(brand_name=code).first_or_404()
    
    if request.method == 'POST':
        try:
            # Update values using names matching edit_hierarchy.html
            brand.brand_name = request.form.get('brand_name', '').strip().upper()
            brand.product_group = request.form.get('product_group', '').strip().upper()
            brand.dept_code = request.form.get('dept_code', '').strip().zfill(3)
            brand.sub_dept_code = request.form.get('sub_dept_code', '').strip().zfill(3)
            brand.class_code = request.form.get('class_code', '').strip().zfill(3)
            
            db.session.commit()
            flash(f"Hierarchy for {brand.brand_name} updated successfully!", "success")
            # FIXED: Added _anchor to persist the Hierarchy tab state
            return redirect(url_for('admin_management', _anchor='hierarchy'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
            
    # Passing variable as 'hierarchy' to match the redesigned edit_hierarchy.html
    return render_template('edit_hierarchy.html', hierarchy=brand)

@hierarchy_bp.route('/admin/delete_hierarchy/<code>', methods=['POST'])
@loggedin_required()
def delete_hierarchy(code):
    """Route to remove a Brand entry from the database."""
    brand = Brand.query.filter_by(brand_name=code).first()
    
    if brand:
        try:
            db.session.delete(brand)
            db.session.commit()
            flash(f"Deleted {code} successfully from Hierarchy.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error deleting record: {e}", "danger")
    else:
        flash("Record not found.", "warning")
        
    # FIXED: Added _anchor to persist the Hierarchy tab state
    return redirect(url_for('admin_management', _anchor='hierarchy'))